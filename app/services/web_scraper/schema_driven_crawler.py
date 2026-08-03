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
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

from app.core.config import settings
from app.services.web_scraper._discovery_helpers import (
    collect_urls_from_nav as _collect_urls_from_nav_helper,
    collect_urls_from_sitemap as _collect_urls_from_sitemap_helper,
    get_sitemap_url_from_robots as _get_sitemap_url_from_robots_helper,
    html_needs_playwright,
)
from app.services.web_scraper.markdown_converter import MarkdownConverter
from app.services.web_scraper.page_classifier import PageClassifier
from app.services.web_scraper.page_schemas import RelevantPage

if TYPE_CHECKING:
    from playwright.async_api import Browser, Playwright

logger = logging.getLogger(__name__)


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
        """Fetch a page with Playwright (full JS execution); fall back to httpx on failure."""
        if not self._browser:
            return None
        try:
            page = await self._browser.new_page()
            try:
                await page.goto(url, wait_until="load", timeout=self.fetch_timeout * 1000)
                return await page.content()
            finally:
                await page.close()
        except Exception as exc:  # noqa: BLE001
            logger.debug(
                "SchemaDrivenCrawler: Playwright render failed for %s (%s): %s — using httpx",
                url, type(exc).__name__, exc,
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
                norm = self._normalize_url(url).split("#", 1)[0]
                if urlparse(norm).netloc == base_domain and norm not in visited:
                    frontier.append((norm, 0.5, 1))

            while frontier and result.pages_crawled < self.max_pages:
                frontier.sort(key=lambda x: x[1], reverse=True)
                current_url, _, current_depth = frontier.pop(0)

                if current_url in visited:
                    continue
                if urlparse(current_url).netloc != base_domain:
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
                for candidate in page.possible_relevant_pages:
                    if candidate.confidence < self.confidence_threshold:
                        continue
                    abs_url = self._normalize_url(urljoin(current_url, candidate.url))
                    abs_url = abs_url.split("#", 1)[0]
                    if abs_url in visited:
                        continue
                    if urlparse(abs_url).netloc != base_domain:
                        continue
                    effective_confidence = candidate.confidence
                    if child_depth > self.max_depth:
                        overshoot = child_depth - self.max_depth
                        effective_confidence = max(
                            0.0, candidate.confidence - self.depth_penalty * overshoot
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
            return await self._fetch_httpx(client, url)

        urls: list[str] = []
        # 1. WordPress sitemap
        try:
            wp = await _collect_urls_from_sitemap_helper(f"{base_url}/wp-sitemap.xml", _fetch_text)
        except Exception as exc:  # noqa: BLE001
            logger.debug("wp-sitemap fetch failed for %s: %s", base_url, exc)
            wp = []
        if wp:
            return wp

        # 2. Generic sitemap
        try:
            generic = await _collect_urls_from_sitemap_helper(f"{base_url}/sitemap.xml", _fetch_text)
        except Exception as exc:  # noqa: BLE001
            logger.debug("sitemap.xml fetch failed for %s: %s", base_url, exc)
            generic = []
        if generic:
            return generic

        # 3. robots.txt Sitemap: directive
        try:
            robots_sitemap = await _get_sitemap_url_from_robots_helper(base_url, _fetch_text)
        except Exception as exc:  # noqa: BLE001
            logger.debug("robots.txt fetch failed for %s: %s", base_url, exc)
            robots_sitemap = None
        if robots_sitemap:
            try:
                robots_urls = await _collect_urls_from_sitemap_helper(robots_sitemap, _fetch_text)
            except Exception as exc:  # noqa: BLE001
                logger.debug("robots sitemap fetch failed for %s: %s", robots_sitemap, exc)
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
        """Fetch HTML for a URL with optional Playwright fallback for JS-rendered pages.

        Mirrors SchoolScraperService's graceful-degradation pattern: plain httpx
        first, and only when the raw HTML contains a known JS-CMS fingerprint do
        we lazily launch Chromium and re-fetch with full JS execution. A
        Playwright failure (or no browser available) silently degrades back to
        the httpx result, so the crawler never hard-fails on a JS page.
        """
        html = await self._fetch_httpx(client, url)
        if html and html_needs_playwright(html):
            await self._ensure_playwright()
            rendered = await self._fetch_text_rendered(url)
            if rendered:
                return rendered
        return html

    async def _fetch_httpx(self, client: httpx.AsyncClient, url: str) -> str | None:
        try:
            resp = await client.get(url)
            if resp.status_code == 200:
                return resp.text
            logger.debug("Non-200 (%s) for %s", resp.status_code, url)
            return None
        except Exception as exc:  # noqa: BLE001
            logger.debug("Fetch failed for %s: %s", url, exc)
            return None

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
