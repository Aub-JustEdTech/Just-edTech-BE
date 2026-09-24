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
from app.services.web_scraper.domain_utils import host_allowed, url_host
from app.services.web_scraper.markdown_converter import MarkdownConverter
from app.services.web_scraper.page_classifier import PageClassifier
from app.services.web_scraper.page_schemas import RelevantPage
from app.services.web_scraper.playwright_interactions import merge_iframe_content
from app.services.web_scraper.url_keywords import (
    _STRONG_KEYWORDS as _MOM_KEYWORDS_STRONG,
    _WEAK_KEYWORDS as _MOM_KEYWORDS_WEAK,
    _WEAK_MIN_HITS,
    is_meeting_related_url,
)

if TYPE_CHECKING:
    from playwright.async_api import Browser, Playwright

logger = logging.getLogger(__name__)

# Confidence bumps applied on top of the LLM's own confidence when pushing a
# candidate link onto the frontier. STRONG matches (unambiguous path segments)
# get a bigger bump than WEAK matches (generic terms that need to co-occur).
# The keyword sets themselves live in url_keywords.py so the media crawl and
# discovery ranking cannot drift apart.
_STRONG_BOOST = 0.25
_WEAK_BOOST = 0.10

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

# Chromium / urllib3 markers that often mean the seed hostname's cert is for
# the apex (or vice versa). Trying the www↔apex rewrite is cheaper than giving
# up, and unlocks Hancock-class CN-mismatch hosts without insecure mode alone.
_SSL_CERT_HOST_MARKERS: tuple[str, ...] = (
    "err_cert_common_name_invalid",
    "certificate_verify_failed",
    "sslcertverificationerror",
    "hostname mismatch",
    "certificate does not match",
)

# JS shells sometimes return large HTML with no extractable markdown via httpx.
# Re-render with Playwright when html is this large (or larger) but markdown
# is empty — Warwick / Old Sturbridge class failures.
_EMPTY_MARKDOWN_PLAYWRIGHT_MIN_HTML = 5_000


def _ssl_cert_host_error(attempt: "FetchAttempt") -> bool:
    """True when a fetch failure looks like a hostname/cert CN mismatch."""
    if attempt.http_status is not None:
        return False
    haystack = " ".join(
        s for s in (attempt.exception_type, attempt.exception_message) if s
    ).lower()
    return any(m in haystack for m in _SSL_CERT_HOST_MARKERS)


def _www_apex_alternates(url: str) -> list[str]:
    """Return the www↔apex hostname rewrite of ``url`` (0 or 1 alternate)."""
    parsed = urlparse(url)
    host = parsed.hostname or ""
    if not host:
        return []
    if host.startswith("www."):
        alt_host = host[4:]
    else:
        alt_host = f"www.{host}"
    if alt_host == host:
        return []
    # Preserve port if present (rare for school sites).
    netloc = alt_host
    if parsed.port:
        netloc = f"{alt_host}:{parsed.port}"
    return [urlunsplit((parsed.scheme, netloc, parsed.path, parsed.query, parsed.fragment))]


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


@dataclass
class FetchAttempt:
    """Outcome of a single HTTP/browser fetch attempt."""

    html: str | None = None
    http_status: int | None = None
    exception_type: str | None = None
    exception_message: str | None = None
    # Final URL after following redirects (httpx ``resp.url`` / Playwright
    # ``page.url``). ``None`` when no response was received. Used by the
    # crawl loop to discover that a SchoolBlocks vanity seed redirected to a
    # different host, so that host can be added to the per-crawl allowlist.
    final_url: str | None = None


@dataclass
class FetchMeta:
    """Aggregated diagnostics from the fetch escalation ladder."""

    stage: str = "httpx"
    http_status: int | None = None
    exception_type: str | None = None
    exception_message: str | None = None
    stages_tried: list[str] = field(default_factory=list)
    # Final URL after redirects of the *successful* attempt in the ladder.
    # Used by the crawl loop to discover that a seed redirected to a new host
    # (e.g. SchoolBlocks vanity -> real district domain) and allowlist that
    # host for the rest of the crawl.
    final_url: str | None = None

    def record_attempt(self, stage: str, attempt: FetchAttempt) -> None:
        """Update meta from the latest attempt (keeps last failure details)."""
        if stage not in self.stages_tried:
            self.stages_tried.append(stage)
        self.stage = stage
        self.http_status = attempt.http_status
        if attempt.exception_type:
            self.exception_type = attempt.exception_type
            self.exception_message = attempt.exception_message
        elif attempt.html is not None:
            # Successful body clears prior exception noise from earlier stages.
            self.exception_type = None
            self.exception_message = None
            # Capture the successful attempt's final URL (may differ from the
            # requested URL after redirects). Cleared on a later failed
            # attempt below.
            if attempt.final_url:
                self.final_url = attempt.final_url
        # Don't let a later failed attempt overwrite a previously captured
        # successful final_url — but if this attempt ALSO has a final_url
        # (e.g. httpx returned a non-200 with a redirect chain), prefer it
        # over the prior value so operators see the most recent redirect.
        if attempt.final_url and attempt.html is None:
            self.final_url = attempt.final_url


@dataclass
class CrawlError:
    """Structured per-URL crawl failure for offline failure analysis."""

    code: str
    url: str
    http_status: int | None = None
    exception_type: str | None = None
    exception_message: str | None = None
    stage: str | None = None
    html_length: int | None = None

    def summary(self) -> str:
        """Compact backward-compatible string (kept on CrawlResult.errors)."""
        parts = [f"{self.code}: {self.url}"]
        extras: list[str] = []
        if self.http_status is not None:
            extras.append(f"status={self.http_status}")
        if self.exception_type:
            if self.exception_message:
                extras.append(f"{self.exception_type}: {self.exception_message}")
            else:
                extras.append(self.exception_type)
        if self.stage:
            extras.append(f"stage={self.stage}")
        if self.html_length is not None:
            extras.append(f"html_len={self.html_length}")
        if extras:
            return f"{parts[0]} ({', '.join(extras)})"
        return parts[0]

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "url": self.url,
            "http_status": self.http_status,
            "exception_type": self.exception_type,
            "exception_message": self.exception_message,
            "stage": self.stage,
            "html_length": self.html_length,
        }


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
    # Compact strings for logs / legacy consumers. Prefer error_details for analysis.
    errors: list[str] = field(default_factory=list)
    error_details: list[CrawlError] = field(default_factory=list)
    # True when the crawl stopped only because max_pages was hit while the
    # frontier still had unvisited candidates — i.e. the page budget cut
    # exploration short. Surfaced on DiscoverResponse so operators can tell
    # "nothing more to find" from "we ran out of budget". False when the
    # frontier emptied naturally (or only failed-fetches remained).
    max_pages_limit_reached: bool = False

    def record_error(self, error: CrawlError) -> None:
        """Append both structured and compact representations of a failure."""
        self.error_details.append(error)
        self.errors.append(error.summary())

    def to_dict(self) -> dict[str, Any]:
        return {
            "seed_url": self.seed_url,
            "pages_crawled": self.pages_crawled,
            "llm_calls": self.llm_calls,
            "data_pages": [p.model_dump() for p in self.data_pages],
            "visited_pages": [p.model_dump() for p in self.visited_pages],
            "errors": self.errors,
            "error_details": [e.to_dict() for e in self.error_details],
            "max_pages_limit_reached": self.max_pages_limit_reached,
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
        # Separate Chromium instance launched with --disable-http2 for the
        # HTTP/1.1 fallback path. Some school servers misconfigure HTTP/2 and
        # fail every HTTP/2 request with ERR_HTTP2_PROTOCOL_ERROR; the default
        # Chromium speaks HTTP/2 by default, so a second browser with HTTP/2
        # disabled is the only way Playwright can reach them. Lazily launched
        # so the common case (no protocol errors) pays no extra browser cost.
        self._http1_browser: "Browser | None" = None

    @staticmethod
    def _chromium_launch_kwargs(*, disable_http2: bool = False) -> dict:
        """Build kwargs for chromium.launch() (system Chromium in Docker).

        ``disable_http2=True`` adds the ``--disable-http2`` Chromium arg used
        by the HTTP/1.1 fallback browser — needed for servers that mis-Negotiate
        HTTP/2 and reject every HTTP/2 request with ERR_HTTP2_PROTOCOL_ERROR.
        """
        kwargs: dict = {"headless": True}
        executable_path = getattr(settings, "PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH", None)
        args: list[str] = []
        if executable_path:
            kwargs["executable_path"] = executable_path
        if executable_path or disable_http2:
            # --no-sandbox is required when running the system Chromium binary
            # installed via apt in the Docker image (PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH).
            # Also keep it on the http1 fallback browser for consistency.
            args.append("--no-sandbox")
        if disable_http2:
            args.append("--disable-http2")
        if args:
            kwargs["args"] = args
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

    async def _ensure_http1_playwright(self) -> "Browser | None":
        """Lazily launch a Chromium instance with HTTP/2 disabled.

        Returns the browser instance, or None if Playwright failed to launch.
        A separate browser (rather than re-launching the default with a flag)
        keeps the common path on the default HTTP/2-capable Chromium and only
        spins up the HTTP/1.1 instance when a protocol error demands it.
        """
        if self._http1_browser:
            return self._http1_browser
        if self._pw is None:
            from playwright.async_api import async_playwright

            self._pw = await async_playwright().start()
        try:
            self._http1_browser = await self._pw.chromium.launch(
                **self._chromium_launch_kwargs(disable_http2=True)
            )
            logger.info(
                "SchemaDrivenCrawler: HTTP/1.1-only Chromium launched for "
                "protocol-error fallback"
            )
            return self._http1_browser
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "SchemaDrivenCrawler: could not launch HTTP/1.1 Chromium: %s", exc
            )
            return None

    # Substrings (case-insensitive) that mark a fetch failure as an HTTP/2
    # protocol incompatibility rather than a generic connection error. The
    # report's dominant homepage hard-fail was ERR_HTTP2_PROTOCOL_ERROR; both
    # httpx and Playwright can surface it (httpx via RemoteProtocolError /
    # httpcore's h2 layer, Playwright via net::ERR_HTTP2_PROTOCOL_ERROR).
    _HTTP2_ERROR_MARKERS: tuple[str, ...] = (
        "err_http2",
        "http2",
        "h2 protocol",
        "remoteprotocolerror",
        "protocol error",
    )

    @classmethod
    def _is_http2_protocol_error(cls, attempt: FetchAttempt) -> bool:
        """True when an attempt's exception looks like an HTTP/2 protocol failure.

        Only connection/protocol failures (``http_status is None``) qualify —
        a real 404/500 is never a protocol error. Detects both httpx's
        ``RemoteProtocolError`` and Playwright's ``net::ERR_HTTP2_PROTOCOL_ERROR``
        strings.
        """
        if attempt.http_status is not None:
            return False
        haystack = " ".join(
            s for s in (attempt.exception_type, attempt.exception_message) if s
        ).lower()
        if not haystack:
            return False
        return any(m in haystack for m in cls._HTTP2_ERROR_MARKERS)

    async def _fetch_text_rendered(
        self, url: str, *, browser: "Browser | None" = None
    ) -> FetchAttempt:
        """Fetch a page with Playwright (full JS execution).

        ``browser`` defaults to the standard lazily-launched Chromium; pass the
        HTTP/1.1-only instance (:meth:`_ensure_http1_playwright`) to retry a
        URL whose default-browser fetch failed with an HTTP/2 protocol error.

        For board-meeting platform URLs (BoardDocs, Diligent, BoardOnTrack) the
        page content is typically injected into nested ``<iframe>``s rather
        than the parent document, so after the page loads we merge the HTML of
        every accessible frame into the parent HTML before returning.
        """
        target = browser if browser is not None else self._browser
        if not target:
            return FetchAttempt(
                exception_type="PlaywrightUnavailable",
                exception_message="browser not launched",
            )
        context = None
        try:
            # Always open the page on ``target`` (HTTP/1.1 browser when passed).
            # ignore_https_errors unlocks CN-mismatch / incomplete-chain hosts
            # that Chromium would otherwise refuse (Hancock-class SSL).
            context = await target.new_context(ignore_https_errors=True)
            page = await context.new_page()
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
                    html = await merge_iframe_content(page, top_url=url)
                else:
                    html = await page.content()
                return FetchAttempt(
                    html=html, http_status=200, final_url=page.url
                )
            finally:
                await page.close()
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "SchemaDrivenCrawler: Playwright render failed for %s (%s): %s",
                url,
                type(exc).__name__,
                exc,
            )
            return FetchAttempt(
                exception_type=type(exc).__name__,
                exception_message=str(exc),
            )
        finally:
            if context is not None:
                try:
                    await context.close()
                except Exception:  # noqa: BLE001
                    pass

    async def close(self) -> None:
        """Close the Playwright browser(s) if launched."""
        for browser_attr in ("_browser", "_http1_browser"):
            browser = getattr(self, browser_attr, None)
            if browser:
                try:
                    await browser.close()
                except Exception:  # noqa: BLE001
                    pass
                setattr(self, browser_attr, None)
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

        # Per-crawl allowlist of hosts the crawler may visit. Starts as just
        # the seed's own host; grows when a fetch follows redirects to a new
        # host (e.g. a SchoolBlocks vanity seed that 301-redirects to the real
        # district domain). A candidate is visitable when its host is in this
        # set, shares the seed's naive registrable domain, or is a board
        # platform (handled separately by is_board_platform_url).
        allowed_hosts: set[str] = {base_domain}

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
                    host_allowed(url_host(norm), seed_host=base_domain, allowed_hosts=allowed_hosts)
                    or is_board_platform_url(norm)
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
                # Same-organization OR an allowlisted off-domain board-meeting
                # platform (single-hop follow — these platforms host meeting
                # minutes/agendas on a different domain than the school site).
                if not host_allowed(
                    current_netloc, seed_host=base_domain, allowed_hosts=allowed_hosts
                ) and not is_board_platform_url(current_url):
                    continue

                visited.add(current_url)
                result.pages_crawled += 1

                html, fetch_meta = await self._fetch(client, current_url)
                # If the fetch followed redirects to a different host (e.g. a
                # SchoolBlocks vanity seed that 301-redirects to the real
                # district domain), allowlist that host for the rest of the
                # crawl so its meeting pages can be visited/enqueued.
                if fetch_meta.final_url:
                    final_host = url_host(fetch_meta.final_url)
                    if final_host and final_host not in allowed_hosts:
                        # Only allowlist hosts that look like the same
                        # organization OR were reached by a redirect from a
                        # page we already chose to visit (the fetch itself
                        # followed the redirect chain with our UA/cookies).
                        # This keeps the open web out while letting CMS
                        # redirect-to-canonical cases through.
                        allowed_hosts.add(final_host)
                        logger.info(
                            "SchemaDrivenCrawler: allowlisting redirect "
                            "target host %s (reached from %s)",
                            final_host,
                            current_url,
                        )
                if not html:
                    result.record_error(
                        CrawlError(
                            code="fetch_failed",
                            url=current_url,
                            http_status=fetch_meta.http_status,
                            exception_type=fetch_meta.exception_type,
                            exception_message=fetch_meta.exception_message,
                            stage=fetch_meta.stage,
                        )
                    )
                    continue

                markdown = self._render_markdown(html, current_url)
                if not markdown.strip():
                    # Large empty-markdown HTML is usually a JS shell fetched
                    # via httpx. Escalate to Playwright once before giving up.
                    if (
                        len(html) >= _EMPTY_MARKDOWN_PLAYWRIGHT_MIN_HTML
                        and (fetch_meta.stage or "") != "playwright"
                        and (fetch_meta.stage or "") != "playwright_http1"
                    ):
                        logger.info(
                            "SchemaDrivenCrawler: empty_markdown (html_len=%d) "
                            "for %s — forcing Playwright re-render",
                            len(html),
                            current_url,
                        )
                        await self._ensure_playwright()
                        rendered = await self._fetch_text_rendered(current_url)
                        fetch_meta.record_attempt("playwright", rendered)
                        if rendered.html:
                            html = rendered.html
                            markdown = self._render_markdown(html, current_url)
                    if not markdown.strip():
                        result.record_error(
                            CrawlError(
                                code="empty_markdown",
                                url=current_url,
                                html_length=len(html),
                                stage=fetch_meta.stage,
                            )
                        )
                        continue

                # Always surface off-site board-portal links (Diligent /
                # BoardOnTrack / Granicus / BoardDocs) into the frontier —
                # Acushnet-class sites have 0 on-domain data pages because
                # minutes live only on the portal.
                for portal_url, portal_conf in self._extract_board_platform_links(
                    html=html,
                    page_url=current_url,
                    visited=visited,
                    existing_frontier_urls={u for u, _, _ in frontier},
                ):
                    frontier.append((portal_url, portal_conf, current_depth + 1))

                try:
                    page = await self.classifier.classify(current_url, markdown, today)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("Classify failed for %s: %s", current_url, exc)
                    result.record_error(
                        CrawlError(
                            code="classify_failed",
                            url=current_url,
                            exception_type=type(exc).__name__,
                            exception_message=str(exc),
                        )
                    )
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
                    if not host_allowed(
                        current_candidate_netloc,
                        seed_host=base_domain,
                        allowed_hosts=allowed_hosts,
                    ) and not is_board_platform_url(abs_url):
                        continue
                    effective_confidence = boosted_confidence
                    if child_depth > self.max_depth:
                        overshoot = child_depth - self.max_depth
                        effective_confidence = max(
                            0.0, boosted_confidence - self.depth_penalty * overshoot
                        )
                    frontier.append((abs_url, effective_confidence, child_depth))

                # Hub-page link harvest: when the LLM marked this page as a
                # navigation hub to meeting pages (has_data_links=True),
                # deterministically extract meeting-related <a href> links
                # from the raw HTML. This is the fix for the report's
                # "same-domain hub — crawl stopped at 1 page" failure mode:
                # the LLM sometimes under-suggests children on a sparse hub
                # (or suggests candidates that later 404), and without a
                # harvest the frontier can die after the hub. Harvested links
                # are deduped against existing frontier URLs (no exact
                # duplication) and enter at confidence 0.55 (+ keyword boost)
                # — below strong LLM hits, so the LLM still drives ordering
                # when it returns good candidates. Skipped for board platforms
                # (their children are session-bound SPA routes, not
                # crawlable <a href> links).
                if page.has_data_links and not skip_child_enqueue:
                    frontier_urls = {u for u, _, _ in frontier}
                    harvested = self._harvest_hub_links(
                        html=html,
                        page_url=current_url,
                        base_domain=base_domain,
                        allowed_hosts=allowed_hosts,
                        visited=visited,
                        existing_frontier_urls=frontier_urls,
                        child_depth=child_depth,
                        max_per_page=10,
                    )
                    for abs_url, eff_conf in harvested:
                        frontier.append((abs_url, eff_conf, child_depth))

        # Did the page budget — not the frontier — stop us? True only when we
        # hit max_pages AND there were still URLs we never got to classify.
        # Entries already in `visited` (fetch_failed / classify_failed still
        # count as visited) are excluded; only genuinely unvisited frontier
        # entries indicate "more to explore, budget ran out".
        if result.pages_crawled >= self.max_pages and any(
            url not in visited for url, _, _ in frontier
        ):
            result.max_pages_limit_reached = True
            logger.warning(
                "SchemaDrivenCrawler: max_pages budget (%d) reached for %s "
                "with unvisited frontier remaining; raising max_pages_limit_reached",
                self.max_pages,
                seed_url,
            )

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
            html, _meta = await self._fetch(client, url)
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
            attempt = await self._fetch_httpx(client, url)
            return attempt.html

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

    async def _fetch(
        self, client: httpx.AsyncClient, url: str
    ) -> tuple[str | None, FetchMeta]:
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

        Returns (html_or_None, FetchMeta) so callers can record why a fetch
        failed (status / exception / stage) instead of a bare fetch_failed.
        """
        meta = FetchMeta()

        # Board platforms: skip the httpx-fingerprint gate and go straight to
        # a real browser. Their content is JS-rendered into iframes, so httpx
        # would only return the SPA shell with no useful text/links.
        if is_board_platform_url(url):
            await self._ensure_playwright()
            rendered = await self._fetch_text_rendered(url)
            meta.record_attempt("playwright", rendered)
            if rendered.html:
                return rendered.html, meta
            # Board platforms are JS-rendered SPAs; if Playwright failed with an
            # HTTP/2 protocol error, try the HTTP/1.1-only Chromium before
            # falling through to httpx.
            if self._is_http2_protocol_error(rendered):
                http1_browser = await self._ensure_http1_playwright()
                if http1_browser is not None:
                    rendered = await self._fetch_text_rendered(
                        url, browser=http1_browser
                    )
                    meta.record_attempt("playwright_http1", rendered)
                    if rendered.html:
                        return rendered.html, meta
            # Fall through to httpx as a last resort if Playwright is
            # unavailable or failed — better a shell than nothing.
            attempt = await self._fetch_httpx(client, url)
            meta.record_attempt("httpx", attempt)
            if attempt.html is None:
                logger.warning(
                    "SchemaDrivenCrawler: fetch_failed for %s "
                    "(status=%s, stage=%s, exc=%s)",
                    url,
                    meta.http_status,
                    meta.stage,
                    meta.exception_type,
                )
            return attempt.html, meta

        attempt = await self._fetch_httpx(client, url)
        meta.record_attempt("httpx", attempt)
        html = attempt.html
        status = attempt.http_status

        # www↔apex rewrite on CN-mismatch / cert hostname errors before UA
        # rotation or Playwright (cheap, often unlocks Hancock-class hosts).
        if html is None and _ssl_cert_host_error(attempt):
            for alt_url in _www_apex_alternates(url):
                logger.info(
                    "SchemaDrivenCrawler: SSL host error for %s — trying %s",
                    url,
                    alt_url,
                )
                alt_attempt = await self._fetch_httpx(client, alt_url)
                meta.record_attempt("httpx_www_apex", alt_attempt)
                if alt_attempt.html is not None:
                    return alt_attempt.html, meta
                # Also try Playwright against the alternate host.
                await self._ensure_playwright()
                rendered = await self._fetch_text_rendered(alt_url)
                meta.record_attempt("playwright_www_apex", rendered)
                if rendered.html:
                    return rendered.html, meta

        # Optional curl_cffi Chrome-impersonation stage for empty-reply /
        # broken-ALPN hosts (HTTP2 cohort). Soft-depends on curl_cffi.
        if html is None and _should_retry(status):
            cffi_attempt = await self._fetch_curl_cffi(url)
            if cffi_attempt is not None:
                meta.record_attempt("curl_cffi", cffi_attempt)
                if cffi_attempt.html is not None:
                    return cffi_attempt.html, meta

        if html is None and _should_retry(status):
            for alt_ua in _ALT_USER_AGENTS:
                attempt = await self._fetch_httpx(
                    client, url, headers={"User-Agent": alt_ua}
                )
                meta.record_attempt("ua_alt", attempt)
                if attempt.html is not None:
                    logger.info(
                        "SchemaDrivenCrawler: default fetch failed (status=%s), "
                        "alt UA succeeded for %s",
                        status,
                        url,
                    )
                    html = attempt.html
                    status = attempt.http_status
                    break
                status = attempt.http_status

        if html and html_needs_playwright(html):
            await self._ensure_playwright()
            rendered = await self._fetch_text_rendered(url)
            meta.record_attempt("playwright", rendered)
            if rendered.html:
                return rendered.html, meta
            # If the default browser failed with an HTTP/2 protocol error on a
            # JS-CMS page, retry once with HTTP/1.1 Chromium before returning
            # the httpx shell HTML.
            if self._is_http2_protocol_error(rendered):
                http1_browser = await self._ensure_http1_playwright()
                if http1_browser is not None:
                    rendered = await self._fetch_text_rendered(
                        url, browser=http1_browser
                    )
                    meta.record_attempt("playwright_http1", rendered)
                    if rendered.html:
                        return rendered.html, meta
            return html, meta

        if html is None and _should_retry(status):
            logger.info(
                "SchemaDrivenCrawler: still failing (status=%s) after UA "
                "rotation for %s — forcing Playwright",
                status,
                url,
            )
            await self._ensure_playwright()
            rendered = await self._fetch_text_rendered(url)
            meta.record_attempt("playwright", rendered)
            if rendered.html:
                return rendered.html, meta

        # HTTP/2 protocol fallback: some school servers misconfigure HTTP/2 and
        # reject every HTTP/2 request with ERR_HTTP2_PROTOCOL_ERROR. httpx
        # (HTTP/1.1 by default) typically already succeeded above in that case,
        # but when the failure came from a JS-CMS page that needed Playwright
        # (which speaks HTTP/2 by default), retry once with a Chromium
        # instance launched with --disable-http2. This is the dominant
        # homepage hard-fail cause in the failure report (~50 schools).
        if html is None and self._is_http2_protocol_error(
            FetchAttempt(
                exception_type=meta.exception_type,
                exception_message=meta.exception_message,
            )
        ):
            logger.info(
                "SchemaDrivenCrawler: HTTP/2 protocol error for %s — "
                "retrying with HTTP/1.1-only Chromium",
                url,
            )
            http1_browser = await self._ensure_http1_playwright()
            if http1_browser is not None:
                rendered = await self._fetch_text_rendered(url, browser=http1_browser)
                meta.record_attempt("playwright_http1", rendered)
                if rendered.html:
                    return rendered.html, meta

        if html is None:
            logger.warning(
                "SchemaDrivenCrawler: fetch_failed for %s "
                "(status=%s, stage=%s, exc=%s: %s)",
                url,
                meta.http_status,
                meta.stage,
                meta.exception_type,
                meta.exception_message,
            )
        return html, meta

    async def _fetch_curl_cffi(self, url: str) -> FetchAttempt | None:
        """Optional Chrome-impersonate fetch via curl_cffi.

        Returns ``None`` when curl_cffi is not installed (so the ladder stays
        intact without a hard dependency). Used as a transport unlock for
        hosts that reject both httpx and Playwright HTTP/2 with empty replies.
        """
        try:
            from curl_cffi.requests import AsyncSession  # type: ignore[import-untyped]
        except ImportError:
            return None
        try:
            async with AsyncSession() as session:
                resp = await session.get(
                    url,
                    impersonate="chrome",
                    timeout=self.fetch_timeout,
                    allow_redirects=True,
                )
                if resp.status_code == 200 and resp.text:
                    return FetchAttempt(
                        html=resp.text,
                        http_status=resp.status_code,
                        final_url=str(getattr(resp, "url", url)),
                    )
                return FetchAttempt(
                    http_status=resp.status_code,
                    final_url=str(getattr(resp, "url", url)),
                )
        except Exception as exc:  # noqa: BLE001
            logger.debug(
                "SchemaDrivenCrawler: curl_cffi failed for %s (%s): %s",
                url,
                type(exc).__name__,
                exc,
            )
            return FetchAttempt(
                exception_type=type(exc).__name__,
                exception_message=str(exc),
            )

    async def _fetch_httpx(
        self,
        client: httpx.AsyncClient,
        url: str,
        *,
        headers: dict[str, str] | None = None,
    ) -> FetchAttempt:
        """Fetch a URL via httpx.

        http_status is None when the request never got an HTTP response at all
        (connection refused, DNS failure, TLS/SSL trust error, timeout) —
        distinct from a real HTTP error status like 404 or 403.
        """
        try:
            resp = await client.get(url, headers=headers)
            if resp.status_code == 200:
                return FetchAttempt(
                    html=resp.text,
                    http_status=resp.status_code,
                    final_url=str(resp.url),
                )
            logger.warning("Non-200 (%s) for %s", resp.status_code, url)
            return FetchAttempt(http_status=resp.status_code, final_url=str(resp.url))
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Fetch failed for %s: %s: %s", url, type(exc).__name__, exc
            )
            return FetchAttempt(
                exception_type=type(exc).__name__,
                exception_message=str(exc),
            )

    def _extract_board_platform_links(
        self,
        *,
        html: str,
        page_url: str,
        visited: set[str],
        existing_frontier_urls: set[str],
        max_per_page: int = 5,
    ) -> list[tuple[str, float]]:
        """Extract Diligent / BoardOnTrack / Granicus / BoardDocs hrefs.

        These portals are off-domain by design; without an explicit harvest
        the same-domain frontier never reaches them and the crawl reports
        0 data pages despite a working school homepage.
        """
        if not html:
            return []
        try:
            soup = BeautifulSoup(html, "html.parser")
        except Exception as exc:  # noqa: BLE001
            logger.debug("board-portal extract failed for %s: %s", page_url, exc)
            return []

        found: list[tuple[str, float]] = []
        seen: set[str] = set()
        for a in soup.find_all("a", href=True):
            href = a["href"].strip()
            if not href or href.startswith(("#", "mailto:", "tel:", "javascript:")):
                continue
            abs_url = self._normalize_url(urljoin(page_url, href))
            abs_url = _percent_encode_url(abs_url.split("#", 1)[0])
            if abs_url in seen or abs_url in visited or abs_url in existing_frontier_urls:
                continue
            if not is_board_platform_url(abs_url):
                continue
            seen.add(abs_url)
            # High confidence: board portals are the primary archive when linked.
            found.append((abs_url, 0.9))
            if len(found) >= max_per_page:
                break
        return found

    def _harvest_hub_links(
        self,
        *,
        html: str,
        page_url: str,
        base_domain: str,
        allowed_hosts: set[str],
        visited: set[str],
        existing_frontier_urls: set[str],
        child_depth: int,
        max_per_page: int,
    ) -> list[tuple[str, float]]:
        """Deterministically extract meeting-related <a href> links from a hub page.

        Used after the LLM candidate loop when the page was classified as a
        navigation hub (``has_data_links=True``). The LLM sometimes under-
        suggests children on a sparse hub, leaving the frontier empty and the
        crawl stuck at one page. This harvest parses the raw HTML directly and
        enqueues any link whose path looks meeting-related
        (:func:`app.services.web_scraper.url_keywords.is_meeting_related_url`),
        gated by the same host policy as LLM candidates.

        Returns ``[(absolute_url, effective_confidence)]`` already filtered
        and deduplicated, sorted by keyword boost (strongest first) and
        capped at ``max_per_page`` so a link-farm hub can't blow the frontier.
        """
        if not html:
            return []
        try:
            soup = BeautifulSoup(html, "html.parser")
        except Exception as exc:  # noqa: BLE001
            logger.debug("hub-harvest parse failed for %s: %s", page_url, exc)
            return []

        # Confidence assigned to harvested links: above the default
        # confidence_threshold (0.5) so they survive the frontier prune, but
        # below strong LLM hits so the LLM still drives ordering when it
        # returns candidates. Keyword boost is added on top so an obviously-
        # relevant path (e.g. /agendas/2025) outranks a generic /minutes one.
        base_harvest_confidence = 0.55
        found: list[tuple[str, float, float]] = []  # (url, eff_conf, boost)
        seen: set[str] = set()
        for a in soup.find_all("a", href=True):
            href = a["href"].strip()
            if not href or href.startswith(("#", "mailto:", "tel:", "javascript:")):
                continue
            abs_url = self._normalize_url(urljoin(page_url, href))
            abs_url = _percent_encode_url(abs_url.split("#", 1)[0])
            if abs_url in seen or abs_url in visited or abs_url in existing_frontier_urls:
                continue
            seen.add(abs_url)
            if not is_meeting_related_url(abs_url):
                continue
            host = urlparse(abs_url).netloc
            if not host_allowed(
                host, seed_host=base_domain, allowed_hosts=allowed_hosts
            ) and not is_board_platform_url(abs_url):
                continue
            boost = _keyword_boost(abs_url)
            eff_conf = min(1.0, base_harvest_confidence + boost)
            if child_depth > self.max_depth:
                overshoot = child_depth - self.max_depth
                eff_conf = max(0.0, eff_conf - self.depth_penalty * overshoot)
            if eff_conf < self.confidence_threshold:
                continue
            found.append((abs_url, eff_conf, boost))

        # Strongest keyword boost first, then by confidence desc.
        found.sort(key=lambda x: (x[2], x[1]), reverse=True)
        return [(u, c) for u, c, _ in found[:max_per_page]]

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
