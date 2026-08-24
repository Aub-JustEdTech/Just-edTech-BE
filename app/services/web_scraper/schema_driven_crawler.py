"""
Schema-driven crawler with a ranked link frontier.

This is the blog's core heuristic:
  1. Start on a seed page.
  2. Ask the LLM to classify it into a RelevantPage (has_data? has_data_links?
     is_archive? which sub-links look promising?).
  3. Push the promising sub-links onto a `to_visit` stack, ranked by confidence.
  4. Pop the highest-confidence link next. Repeat until the frontier is empty
     or a budget (max_pages) is hit.

All decision logic lives here — the LLM only does structured extraction.

Promoted from scripts/school_data/schema_crawl_poc/crawler.py. The POC scripts
now import from here so the two cannot drift.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date
from typing import TYPE_CHECKING, Any
from urllib.parse import quote, unquote, urljoin, urlparse, urlsplit, urlunsplit

import httpx
from bs4 import BeautifulSoup

from app.core.config import settings
from app.services.web_scraper._discovery_helpers import (
    collect_urls_from_nav as _collect_urls_from_nav_helper,
    collect_urls_from_sitemap as _collect_urls_from_sitemap_helper,
    get_sitemap_url_from_robots as _get_sitemap_url_from_robots_helper,
    html_needs_playwright,
)
from app.services.web_scraper.board_platforms import is_board_platform_url
from app.services.web_scraper.markdown_converter import MarkdownConverter
from app.services.web_scraper.page_classifier import PageClassifier
from app.services.web_scraper.page_schemas import RelevantPage
from app.services.web_scraper.playwright_interactions import merge_iframe_content

if TYPE_CHECKING:
    from playwright.async_api import Browser, Playwright

logger = logging.getLogger(__name__)

# Deterministic URL-path keyword boost applied on top of the LLM's own
# confidence when pushing a candidate link onto the frontier. The LLM is
# good at structured extraction but is inconsistent at ranking — a page
# whose URL path literally contains "meeting-minutes" or "board-of-trustees"
# should be visited early even if the LLM was cautious about the link text.
# STRONG matches (unambiguous path segments) get a bigger bump than WEAK
# matches (generic terms that need to co-occur to mean anything).
_MOM_KEYWORDS_STRONG: tuple[str, ...] = (
    "meeting-minutes",
    "meeting_minutes",
    "meetingminutes",
    "agendas-minutes",
    "agendas_minutes",
    "minutes-agendas",
    "minutes-archive",
    "minutes_archive",
    "meeting-packets",
    "meeting_packets",
    "board-packets",
    "archived-agendas",
    "archived_agendas",
    "document-archives",
    "document_archives",
    "board-of-trustees",
    "board_of_trustees",
    "school-committee",
    "school_committee",
    "supervisory_committee",
    "supervisory-committee",
)
_MOM_KEYWORDS_WEAK: tuple[str, ...] = (
    "minutes",
    "agenda",
    "committee",
    "board",
    "trustees",
    "archive",
    "packets",
)
_STRONG_BOOST = 0.25
_WEAK_BOOST = 0.10
_WEAK_MIN_HITS = 2

# HTTP status codes that indicate a WAF/bot-protection block rather than a
# genuine "page doesn't exist" response. On these we retry with an alternate
# User-Agent before giving up, and — if still blocked — escalate to a real
# Playwright browser context, which carries a full browser fingerprint (not
# just a UA string) and bypasses many WAF rules that simple UA-sniffing
# httpx requests trip.
_BLOCKED_STATUS_CODES: frozenset[int] = frozenset({403, 429, 503})

# Alternate User-Agents tried (in order) when the default
# settings.SCHOOL_SCRAPER_USER_AGENT (curl-style, by design — see
# school_scraper_service.py) gets blocked. Some stricter WAF configs
# (Wordfence "aggressive", Cloudflare bot-fight mode) block generic
# tool/browser UAs inconsistently, so we rotate through a couple of
# realistic desktop-browser UAs before falling back to Playwright.
_ALT_USER_AGENTS: tuple[str, ...] = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.4 Safari/605.1.15",
)


def _percent_encode_url(url: str) -> str:
    """Percent-encode an absolute URL's path/query so it's safe to open directly.

    Link hrefs extracted straight from raw HTML/markdown by the LLM (or by
    BeautifulSoup nav-crawling) often contain literal unencoded characters —
    most commonly spaces in a PDF filename, e.g.:
        /UserFiles/.../Meeting Minutes/22-23/SC Minutes 7-19-22.pdf
    Browsers silently encode these when following an <a href>, but the raw
    string is not a valid, independently-clickable URL, and httpx will
    reject or mishandle it as an outgoing request target (surfacing as a
    spurious "fetch_failed" for a page that actually exists).

    We unquote first so any segment that's already percent-encoded (e.g. an
    existing "%20") is not double-encoded into "%2520", then re-quote.
    """
    parts = urlsplit(url)
    path = quote(unquote(parts.path), safe="/%():@!$&'*+,;=~")
    query = quote(unquote(parts.query), safe="=&%():@!$'*+,;~/")
    return urlunsplit((parts.scheme, parts.netloc, path, query, parts.fragment))


def _should_retry(status: int | None) -> bool:
    """Whether a failed fetch is worth retrying with a different UA/browser.

    True for WAF-style blocks (_BLOCKED_STATUS_CODES) AND for connection-level
    failures (status is None — DNS, TLS/SSL trust, timeout) since a real
    browser's network stack can succeed where httpx's did not (different CA
    trust store, TLS fingerprint, etc.). False for genuine HTTP errors like
    404 that no UA/browser change would fix.
    """
    return status is None or status in _BLOCKED_STATUS_CODES


def _keyword_boost(url: str) -> float:
    """Deterministic confidence bump based on meeting-minutes keywords in the URL path.

    Applied additively to the LLM's per-link confidence before the link enters
    the frontier, so obviously-relevant paths (e.g. .../school-committee/
    agendas-minutes or .../board-of-trustees/) get crawled early even when the
    LLM under-scores the surrounding link text.
    """
    path = urlparse(url).path.lower()
    if any(k in path for k in _MOM_KEYWORDS_STRONG):
        return _STRONG_BOOST
    if sum(1 for k in _MOM_KEYWORDS_WEAK if k in path) >= _WEAK_MIN_HITS:
        return _WEAK_BOOST
    return 0.0


@dataclass
class CrawlResult:
    """Result of crawling one seed URL."""

    seed_url: str
    pages_crawled: int
    data_pages: list[RelevantPage] = field(default_factory=list)
    visited_pages: list[RelevantPage] = field(default_factory=list)
    llm_calls: int = 0
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "seed_url": self.seed_url,
            "pages_crawled": self.pages_crawled,
            "llm_calls": self.llm_calls,
            "data_pages": [p.model_dump() for p in self.data_pages],
            "visited_pages": [p.model_dump() for p in self.visited_pages],
            "errors": self.errors,
        }


class SchemaDrivenCrawler:
    """Crawl a school site using LLM page classifications instead of keyword URL matching."""

    def __init__(
        self,
        classifier: PageClassifier | None = None,
        markdown_converter: MarkdownConverter | None = None,
        *,
        max_pages: int = 10,
        max_depth: int = 3,
        depth_penalty: float = 0.15,
        confidence_threshold: float = 0.5,
        fetch_timeout_s: int | None = None,
        user_agent: str | None = None,
        skip_archival: bool = True,
    ):
        self.classifier = classifier or PageClassifier()
        self.md_converter = markdown_converter or MarkdownConverter()
        self.max_pages = max_pages
        self.max_depth = max_depth
        self.depth_penalty = depth_penalty
        self.confidence_threshold = confidence_threshold
        self.fetch_timeout = fetch_timeout_s or settings.WEB_SCRAPER_TIMEOUT_SECONDS
        self.user_agent = user_agent or settings.SCHOOL_SCRAPER_USER_AGENT
        self.skip_archival = skip_archival
        # Lazily-launched Playwright browser for JS-rendered pages. Mirrors
        # SchoolScraperService's graceful-degradation pattern: httpx first, and
        # only when the raw HTML contains a known JS-CMS fingerprint do we
        # launch Chromium and re-fetch with full JS execution.
        self._pw: "Playwright | None" = None
        self._browser: "Browser | None" = None

    @staticmethod
    def _chromium_launch_kwargs() -> dict:
        """Build kwargs for chromium.launch() (system Chromium in Docker)."""
        kwargs: dict = {"headless": True}
        executable_path = getattr(settings, "PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH", None)
        if executable_path:
            kwargs["executable_path"] = executable_path
            kwargs["args"] = ["--no-sandbox"]
        return kwargs

    async def _ensure_playwright(self) -> None:
        """Lazily launch Playwright Chromium. Idempotent; no-op if already running."""
        if self._browser:
            return
        from playwright.async_api import async_playwright

        self._pw = await async_playwright().start()
        self._browser = await self._pw.chromium.launch(**self._chromium_launch_kwargs())
        logger.info(
            "SchemaDrivenCrawler: Playwright Chromium auto-launched — JS-rendered page detected"
        )

    async def _fetch_text_rendered(self, url: str) -> str | None:
        """Fetch a page with Playwright (full JS execution); fall back to httpx on failure.

        For board-meeting platform URLs (BoardDocs, Diligent, BoardOnTrack) the
        page content is typically injected into nested ``<iframe>``s rather
        than the parent document, so after the page loads we merge the HTML of
        every accessible frame into the parent HTML before returning.
        """
        if not self._browser:
            return None
        try:
            page = await self._browser.new_page()
            try:
                # Board platforms are SPAs that set session cookies + render
                # content via XHR after the initial HTML loads; networkidle
                # waits for that post-load activity to settle. Plain pages
                # (same-domain school sites) keep the cheaper "load" wait.
                wait_until = "networkidle" if is_board_platform_url(url) else "load"
                # networkidle can stall on long-polling apps; cap at the
                # configured per-request timeout either way.
                await page.goto(
                    url, wait_until=wait_until, timeout=self.fetch_timeout * 1000
                )
                if is_board_platform_url(url):
                    # Merge iframe content (the real meeting/agenda HTML lives
                    # inside nested frames on these platforms). Falls back to
                    # parent-only HTML if every frame.content() raises.
                    return await merge_iframe_content(page, top_url=url)
                return await page.content()
            finally:
                await page.close()
        except Exception as exc:  # noqa: BLE001
            logger.debug(
                "SchemaDrivenCrawler: Playwright render failed for %s (%s): %s — using httpx",
                url,
                type(exc).__name__,
                exc,
            )
            return None

    async def close(self) -> None:
        """Close the Playwright browser if it was launched."""
        if self._browser:
            try:
                await self._browser.close()
            except Exception:  # noqa: BLE001
                pass
            self._browser = None
        if self._pw:
            try:
                await self._pw.stop()
            except Exception:  # noqa: BLE001
                pass
            self._pw = None

    async def crawl(self, seed_url: str, today: date | None = None) -> CrawlResult:
        """Crawl from a seed URL, returning all discovered data pages."""
        today = today or date.today()
        seed_url = self._normalize_url(seed_url)
        parsed_seed = urlparse(seed_url)
        base_domain = parsed_seed.netloc
        base_url = f"{parsed_seed.scheme}://{parsed_seed.netloc}"

        result = CrawlResult(seed_url=seed_url, pages_crawled=0)

        # Ranked frontier: list of (url, confidence). We pop the highest
        # confidence first by sorting on each iteration — the frontier is
        # small (bounded by max_pages * N candidate links per page), so an
        # O(n log n) sort per step is negligible vs. the LLM call cost.
        #
        # Seed the frontier from the site's sitemap / robots.txt / nav (same
        # machinery SchoolScraperService uses) before starting the LLM loop.
        # This fixes sitemap-only sites where the homepage has no crawlable
        # links but the sitemap has hundreds. Each seed URL gets a default
        # confidence of 0.5; the LLM re-ranks as it classifies. The seed URL
        # itself stays at confidence 1.0 so it's crawled first.
        # Frontier entries: (url, effective_confidence, depth).
        # depth=0 is the seed page. Links found on a depth-N page are depth N+1.
        # Beyond max_depth, confidence is penalised by depth_penalty per extra hop
        # so the crawler prefers closer pages but can still reach deeper ones if
        # nothing closer scores well.
        frontier: list[tuple[str, float, int]] = [(seed_url, 1.0, 0)]
        visited: set[str] = set()

        async with httpx.AsyncClient(
            timeout=httpx.Timeout(self.fetch_timeout, connect=10.0),
            follow_redirects=True,
            headers={"User-Agent": self.user_agent},
        ) as client:
            seed_urls = await self._collect_seed_frontier(client, base_url, base_domain)
            for url in seed_urls:
                norm = _percent_encode_url(self._normalize_url(url).split("#", 1)[0])
                if (
                    urlparse(norm).netloc == base_domain or is_board_platform_url(norm)
                ) and norm not in visited:
                    # Sitemap/nav URLs get a base confidence of 0.5, boosted
                    # deterministically if the path itself already looks like
                    # a meeting-minutes page — this lets obviously-relevant
                    # sitemap entries jump the queue before any LLM call.
                    frontier.append((norm, min(1.0, 0.5 + _keyword_boost(norm)), 1))

            while frontier and result.pages_crawled < self.max_pages:
                frontier.sort(key=lambda x: x[1], reverse=True)
                current_url, _, current_depth = frontier.pop(0)

                if current_url in visited:
                    continue
                current_netloc = urlparse(current_url).netloc
                # Same-domain OR an allowlisted off-domain board-meeting
                # platform (single-hop follow — these platforms host meeting
                # minutes/agendas on a different domain than the school site).
                if current_netloc != base_domain and not is_board_platform_url(
                    current_url
                ):
                    continue

                visited.add(current_url)
                result.pages_crawled += 1

                html = await self._fetch(client, current_url)
                if not html:
                    result.errors.append(f"fetch_failed: {current_url}")
                    continue

                markdown = self._render_markdown(html, current_url)
                if not markdown.strip():
                    result.errors.append(f"empty_markdown: {current_url}")
                    continue

                try:
                    page = await self.classifier.classify(current_url, markdown, today)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("Classify failed for %s: %s", current_url, exc)
                    result.errors.append(f"classify_failed: {current_url}: {exc}")
                    continue
                result.llm_calls += 1
                result.visited_pages.append(page)

                if page.has_data and page.data_page_info:
                    if self.skip_archival and page.data_page_info.is_archive:
                        logger.info(
                            "Skipping archival data page: %s (years=%s)",
                            current_url,
                            page.data_page_info.data_years_available,
                        )
                    else:
                        result.data_pages.append(page)

                child_depth = current_depth + 1
                # Board platforms are a single-hop visit only — don't enqueue
                # their own discovered sub-links. The crawl descends into the
                # foreign platform once (to fetch + classify the linked page),
                # then returns to the school domain for further exploration.
                skip_child_enqueue = is_board_platform_url(current_url)
                for candidate in page.possible_relevant_pages:
                    abs_url = self._normalize_url(urljoin(current_url, candidate.url))
                    abs_url = _percent_encode_url(abs_url.split("#", 1)[0])
                    # Persist the resolved, absolute, percent-encoded URL back
                    # onto the candidate so JSON exports / API responses carry
                    # a URL that's independently valid and clickable — not the
                    # LLM's raw (often relative, unencoded) extracted href.
                    candidate.url = abs_url
                    if skip_child_enqueue:
                        continue
                    boosted_confidence = min(
                        1.0, candidate.confidence + _keyword_boost(abs_url)
                    )
                    if boosted_confidence < self.confidence_threshold:
                        continue
                    if abs_url in visited:
                        continue
                    current_candidate_netloc = urlparse(abs_url).netloc
                    if (
                        current_candidate_netloc != base_domain
                        and not is_board_platform_url(abs_url)
                    ):
                        continue
                    effective_confidence = boosted_confidence
                    if child_depth > self.max_depth:
                        overshoot = child_depth - self.max_depth
                        effective_confidence = max(
                            0.0, boosted_confidence - self.depth_penalty * overshoot
                        )
                    frontier.append((abs_url, effective_confidence, child_depth))

        return result

    async def fetch_markdown(self, url: str) -> str | None:
        """Fetch a single URL and render it as markdown-with-links.

        Standalone single-page fetch path (spins up its own httpx client) so
        the eval harness can render a page exactly the way the crawler would,
        without running a full crawl. Returns None on fetch failure / non-200.
        Uses the same _render_markdown as crawl() so the LLM sees identical
        input shape.
        """
        url = self._normalize_url(url)
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(self.fetch_timeout, connect=10.0),
            follow_redirects=True,
            headers={"User-Agent": self.user_agent},
        ) as client:
            html = await self._fetch(client, url)
        if not html:
            return None
        return self._render_markdown(html, url)

    async def _collect_seed_frontier(
        self,
        client: httpx.AsyncClient,
        base_url: str,
        base_domain: str,
    ) -> list[str]:
        """Collect candidate seed URLs from sitemap / robots.txt / nav.

        Mirrors SchoolScraperService.discover_candidate_urls' discovery
        priority (wp-sitemap → sitemap.xml → robots.txt → nav-crawl), but
        returns the raw URL pool without keyword filtering — the LLM does the
        ranking during the crawl. Falls back to an empty list (which leaves
        just the seed URL in the frontier) on any failure.
        """

        async def _fetch_text(url: str) -> str | None:
            html, _status = await self._fetch_httpx(client, url)
            return html

        urls: list[str] = []
        # 1. WordPress sitemap
        try:
            wp = await _collect_urls_from_sitemap_helper(
                f"{base_url}/wp-sitemap.xml", _fetch_text
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("wp-sitemap fetch failed for %s: %s", base_url, exc)
            wp = []
        if wp:
            return wp

        # 2. Generic sitemap
        try:
            generic = await _collect_urls_from_sitemap_helper(
                f"{base_url}/sitemap.xml", _fetch_text
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("sitemap.xml fetch failed for %s: %s", base_url, exc)
            generic = []
        if generic:
            return generic

        # 3. robots.txt Sitemap: directive
        try:
            robots_sitemap = await _get_sitemap_url_from_robots_helper(
                base_url, _fetch_text
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("robots.txt fetch failed for %s: %s", base_url, exc)
            robots_sitemap = None
        if robots_sitemap:
            try:
                robots_urls = await _collect_urls_from_sitemap_helper(
                    robots_sitemap, _fetch_text
                )
            except Exception as exc:  # noqa: BLE001
                logger.debug(
                    "robots sitemap fetch failed for %s: %s", robots_sitemap, exc
                )
                robots_urls = []
            if robots_urls:
                return robots_urls

        # 4. Nav-crawl fallback (no Playwright in the seeding pass — httpx only;
        #    JS-rendered nav will be picked up when the LLM visits the homepage).
        try:
            nav_urls = await _collect_urls_from_nav_helper(base_url, _fetch_text)
        except Exception as exc:  # noqa: BLE001
            logger.debug("nav-crawl failed for %s: %s", base_url, exc)
            nav_urls = []
        return nav_urls

    async def _fetch(self, client: httpx.AsyncClient, url: str) -> str | None:
        """Fetch HTML for a URL, escalating through UA rotation and Playwright as needed.

        Escalation ladder (each step only runs if the previous one failed):
          1. Plain httpx with the default UA (curl-style — see
             SCHOOL_SCRAPER_USER_AGENT).
          2. If blocked (403/429/503) or the connection itself failed
             (status=None — DNS, TLS/SSL trust errors, timeouts): retry with
             each of _ALT_USER_AGENTS in turn. Some WAFs only block the
             default UA pattern; this step is a no-op for connection-level
             failures but is cheap to try.
          3. If the HTML that came back needs JS rendering (known SPA/CMS
             fingerprint): launch Playwright Chromium and re-fetch.
          4. If still failing after (1)+(2) — whether a WAF block or a
             connection/TLS failure — force Playwright even without a JS-CMS
             fingerprint. A real browser context has its own network stack
             (its own CA trust store, full TLS/JS fingerprint, not just a UA
             header) and clears many failures plain httpx cannot: WAF
             challenges AND sites whose certificate chain isn't recognized
             by the Python process's CA bundle but is a valid, trusted chain
             from a real browser's perspective.

        Board-meeting platform URLs (BoardDocs, Diligent Community,
        BoardOnTrack) are always fetched with Playwright directly (skipping
        the httpx-first gate) because they are JS/iframe-heavy SPAs whose
        meaningful content is not present in the raw httpx response body.

        A genuine "page not found" (404 etc., not in _RETRY_STATUS_CODES) is
        NOT retried — only failures that a browser/UA change could plausibly
        fix. A Playwright failure (or no browser available) silently degrades
        back to the last httpx result, so the crawler never hard-fails
        outright.
        """
        # Board platforms: skip the httpx-fingerprint gate and go straight to
        # a real browser. Their content is JS-rendered into iframes, so httpx
        # would only return the SPA shell with no useful text/links.
        if is_board_platform_url(url):
            await self._ensure_playwright()
            rendered = await self._fetch_text_rendered(url)
            if rendered:
                return rendered
            # Fall through to httpx as a last resort if Playwright is
            # unavailable or failed — better a shell than nothing.
            html, status = await self._fetch_httpx(client, url)
            return html

        html, status = await self._fetch_httpx(client, url)

        if html is None and _should_retry(status):
            for alt_ua in _ALT_USER_AGENTS:
                html, status = await self._fetch_httpx(
                    client, url, headers={"User-Agent": alt_ua}
                )
                if html is not None:
                    logger.info(
                        "SchemaDrivenCrawler: default fetch failed (status=%s), "
                        "alt UA succeeded for %s",
                        status,
                        url,
                    )
                    break

        if html and html_needs_playwright(html):
            await self._ensure_playwright()
            rendered = await self._fetch_text_rendered(url)
            if rendered:
                return rendered
            return html

        if html is None and _should_retry(status):
            logger.info(
                "SchemaDrivenCrawler: still failing (status=%s) after UA "
                "rotation for %s — forcing Playwright",
                status,
                url,
            )
            await self._ensure_playwright()
            rendered = await self._fetch_text_rendered(url)
            if rendered:
                return rendered

        return html

    async def _fetch_httpx(
        self,
        client: httpx.AsyncClient,
        url: str,
        *,
        headers: dict[str, str] | None = None,
    ) -> tuple[str | None, int | None]:
        """Fetch a URL via httpx. Returns (body_text_or_None, status_code_or_None).

        status is None when the request never got an HTTP response at all
        (connection refused, DNS failure, TLS/SSL trust error, timeout) —
        distinct from a real HTTP error status like 404 or 403.
        """
        try:
            resp = await client.get(url, headers=headers)
            if resp.status_code == 200:
                return resp.text, resp.status_code
            logger.debug("Non-200 (%s) for %s", resp.status_code, url)
            return None, resp.status_code
        except Exception as exc:  # noqa: BLE001
            logger.debug("Fetch failed for %s: %s: %s", url, type(exc).__name__, exc)
            return None, None

    def _render_markdown(self, html: str, url: str) -> str:
        """Render page HTML as markdown-with-links, preserving link text + href.

        We deliberately keep <nav>/<header>/<aside> in the markdown (the
        MarkdownConverter strips them), because the LLM needs to see the
        navigation links to suggest `possible_relevant_pages`. We only strip
        <script>/<style>.
        """
        soup = BeautifulSoup(html, "html.parser")
        for tag in soup.find_all(["script", "style"]):
            tag.decompose()
        # html2text preserves <a href> as [text](url) — exactly the format
        # the blog uses for "page_text with links as markdown".
        return self.md_converter.converter.handle(str(soup)).strip()

    @staticmethod
    def _normalize_url(url: str) -> str:
        url = url.strip().rstrip("/")
        if not url.startswith(("http://", "https://")):
            url = f"https://{url}"
        return url
