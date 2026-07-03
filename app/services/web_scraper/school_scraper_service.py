"""
Generalised school website scraper.

Two-step flow:
  1. discover_candidate_urls – finds meeting-minutes-style URLs from a school site
     using sitemaps (WP / generic / robots.txt) with a nav-crawl fallback, then
     a targeted follow-up crawl on the top candidates to surface deeper sub-pages.
     When SCHOOL_SCRAPER_USE_PLAYWRIGHT=true the follow-up crawl uses a headless
     Chromium browser so that JavaScript-rendered navigation (e.g. Finalsite CMS
     in-section sidebars) is visible and deeper pages can be discovered.
  2. scrape_media_files – extracts audio, video and document links from a confirmed
     page, optionally following same-domain sub-pages up to a configurable depth.
"""

import logging
import xml.etree.ElementTree as ET
from typing import TYPE_CHECKING
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

from app.core.config import settings

if TYPE_CHECKING:
    from playwright.async_api import Browser, Playwright

logger = logging.getLogger(__name__)

_SITEMAP_NS = "http://www.sitemaps.org/schemas/sitemap/0.9"

# Intentionally do not spoof a browser User-Agent.
# Several school websites (e.g. WordPress-based) return 403 for common
# Chrome/Firefox UA strings while allowing the default python-httpx UA.
_DEFAULT_HEADERS: dict[str, str] = {}


class SchoolScraperService:
    """Service for discovering meeting-archive URLs and scraping media files."""

    def __init__(self, timeout: int | None = None, use_playwright: bool | None = None):
        self.timeout = timeout or settings.WEB_SCRAPER_TIMEOUT_SECONDS
        self.use_playwright = (
            use_playwright
            if use_playwright is not None
            else settings.SCHOOL_SCRAPER_USE_PLAYWRIGHT
        )
        self.client = httpx.AsyncClient(
            timeout=httpx.Timeout(self.timeout, connect=10.0),
            follow_redirects=True,
            headers=_DEFAULT_HEADERS,
        )
        self._pw: "Playwright | None" = None
        self._browser: "Browser | None" = None

    async def __aenter__(self) -> "SchoolScraperService":
        if self.use_playwright:
            from playwright.async_api import async_playwright

            self._pw = await async_playwright().start()
            self._browser = await self._pw.chromium.launch(headless=True)
            logger.debug("Playwright Chromium browser launched")
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        await self.close()

    async def close(self) -> None:
        if self._browser:
            await self._browser.close()
            self._browser = None
        if self._pw:
            await self._pw.stop()
            self._pw = None
        await self.client.aclose()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _normalize_base_url(url: str) -> str:
        url = url.strip().rstrip("/")
        if not url.startswith(("http://", "https://")):
            url = f"https://{url}"
        return url

    async def _fetch_text(self, url: str) -> str | None:
        """Fetch text content from a URL; returns None on any failure."""
        try:
            response = await self.client.get(url)
            if response.status_code == 200:
                return response.text
            logger.debug("Non-200 status %s for %s", response.status_code, url)
            return None
        except Exception as exc:
            logger.debug("Failed to fetch %s (%s): %s", url, type(exc).__name__, exc)
            return None

    async def _fetch_text_rendered(self, url: str) -> str | None:
        """
        Fetch a page's fully JS-rendered HTML using a Playwright browser page.

        Falls back to plain httpx if the browser is not available.
        Uses 'networkidle' to wait until all JS-triggered requests settle so that
        dynamically injected navigation (e.g. Finalsite in-section sidebars) is
        present in the returned HTML.
        """
        if not self._browser:
            return await self._fetch_text(url)
        try:
            page = await self._browser.new_page()
            try:
                await page.goto(
                    url,
                    wait_until="networkidle",
                    timeout=self.timeout * 1000,
                )
                html = await page.content()
            finally:
                await page.close()
            return html
        except Exception as exc:
            logger.debug(
                "Playwright render failed for %s (%s): %s — falling back to httpx",
                url,
                type(exc).__name__,
                exc,
            )
            return await self._fetch_text(url)

    # ------------------------------------------------------------------
    # Sitemap helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_sitemap_xml(xml_text: str) -> tuple[list[str], list[str]]:
        """
        Parse a sitemap XML string.

        Returns:
            (page_urls, child_sitemap_urls) — child_sitemap_urls is non-empty
            only when the document is a <sitemapindex>.
        """
        page_urls: list[str] = []
        child_sitemaps: list[str] = []

        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError as exc:
            logger.debug("XML parse error: %s", exc)
            return page_urls, child_sitemaps

        # Strip namespace prefix to get bare tag name
        tag = root.tag.split("}")[-1] if "}" in root.tag else root.tag
        ns = {"sm": _SITEMAP_NS}

        if tag == "sitemapindex":
            for sitemap_elem in root.findall("sm:sitemap", ns):
                loc = sitemap_elem.findtext("sm:loc", namespaces=ns)
                if loc:
                    child_sitemaps.append(loc.strip())
        elif tag == "urlset":
            for url_elem in root.findall("sm:url", ns):
                loc = url_elem.findtext("sm:loc", namespaces=ns)
                if loc:
                    page_urls.append(loc.strip())

        return page_urls, child_sitemaps

    async def _collect_urls_from_sitemap(self, sitemap_url: str) -> list[str]:
        """
        Fetch a sitemap URL and recursively fetch any child sitemaps
        (one extra level for sitemap-index files).
        """
        all_urls: list[str] = []
        xml_text = await self._fetch_text(sitemap_url)
        if not xml_text:
            return all_urls

        page_urls, child_sitemaps = self._parse_sitemap_xml(xml_text)
        all_urls.extend(page_urls)

        for child_url in child_sitemaps:
            child_text = await self._fetch_text(child_url)
            if child_text:
                child_page_urls, _ = self._parse_sitemap_xml(child_text)
                all_urls.extend(child_page_urls)

        return all_urls

    async def _get_sitemap_url_from_robots(self, base_url: str) -> str | None:
        """Inspect robots.txt for a `Sitemap:` directive."""
        robots_text = await self._fetch_text(f"{base_url}/robots.txt")
        if not robots_text:
            return None
        for line in robots_text.splitlines():
            if line.lower().startswith("sitemap:"):
                return line.split(":", 1)[1].strip()
        return None

    # ------------------------------------------------------------------
    # Nav-crawl fallback
    # ------------------------------------------------------------------

    async def _collect_urls_from_nav(self, base_url: str) -> list[str]:
        """
        Fetch the homepage and extract same-domain links found inside
        <nav> / <header> / <ul> elements, then fall back to all <a> tags.
        """
        html = await self._fetch_text(base_url)
        if not html:
            return []

        soup = BeautifulSoup(html, "html.parser")
        parsed_base = urlparse(base_url)
        base_domain = parsed_base.netloc

        seen: set[str] = set()
        urls: list[str] = []

        containers = soup.find_all(["nav", "header", "ul"]) or [soup]
        for container in containers:
            for a_tag in container.find_all("a", href=True):
                href = str(a_tag["href"]).strip()
                full_url = urljoin(base_url, href)
                parsed = urlparse(full_url)
                if parsed.netloc == base_domain and full_url not in seen:
                    seen.add(full_url)
                    urls.append(full_url)

        return urls

    # ------------------------------------------------------------------
    # Candidate follow-up crawl
    # ------------------------------------------------------------------

    async def _follow_candidate_subpages(
        self,
        candidates: list[dict],
        base_domain: str,
        max_pages: int,
    ) -> list[str]:
        """
        Fetch the top candidate pages and collect additional same-domain links
        from them.

        This is crucial for sites whose sitemaps / homepage nav only link to a
        section root (e.g. /school-committee/about) while the actual
        meeting-archive page is one level deeper (/school-committee/meeting-archives).

        Returns a flat deduplicated list of discovered URLs.
        """
        extra_urls: list[str] = []
        seen: set[str] = set()

        for candidate in candidates[:max_pages]:
            html = await self._fetch_text_rendered(candidate["url"])
            if not html:
                continue

            soup = BeautifulSoup(html, "html.parser")
            for a_tag in soup.find_all("a", href=True):
                href = str(a_tag["href"]).strip()
                full_url = urljoin(candidate["url"], href)
                parsed = urlparse(full_url)
                if parsed.netloc == base_domain and full_url not in seen:
                    seen.add(full_url)
                    extra_urls.append(full_url)

        return extra_urls

    # ------------------------------------------------------------------
    # Keyword filtering
    # ------------------------------------------------------------------

    @staticmethod
    def _filter_candidates(
        urls: list[str],
        keywords: list[str],
        max_candidates: int,
    ) -> list[dict]:
        """Return top-N URLs whose path contains at least one keyword."""
        raw: list[dict] = []
        for url in urls:
            path = urlparse(url).path.lower()
            matched = [kw for kw in keywords if kw in path]
            if matched:
                raw.append({"url": url, "matched_keywords": matched, "score": len(matched)})

        # Deduplicate and sort by score descending
        seen: set[str] = set()
        unique: list[dict] = []
        for entry in sorted(raw, key=lambda x: x["score"], reverse=True):
            if entry["url"] not in seen:
                seen.add(entry["url"])
                unique.append(entry)

        return unique[:max_candidates]

    # ------------------------------------------------------------------
    # Public API — discovery
    # ------------------------------------------------------------------

    async def discover_candidate_urls(
        self,
        base_url: str,
        max_candidates: int | None = None,
    ) -> dict:
        """
        Discover candidate meeting-archive URLs from a school website.

        Discovery priority:
          1. /wp-sitemap.xml  (WordPress sitemap index)
          2. /sitemap.xml     (generic sitemap / sitemap index)
          3. robots.txt       (Sitemap: directive)
          4. Homepage nav-crawl fallback

        After the initial URL pool is built, a targeted follow-up crawl fetches
        the top candidate pages and collects their sub-links — this surfaces
        pages like /school-committee/meeting-archives that are one level below
        what appears in the sitemap or homepage nav.

        Returns a dict with keys: base_url, discovery_method,
        total_urls_scanned, candidates.
        """
        base_url = self._normalize_base_url(base_url)
        max_candidates = max_candidates or settings.SCHOOL_SCRAPER_MAX_CANDIDATES
        keywords = settings.SCHOOL_SCRAPER_MEETING_KEYWORDS
        base_domain = urlparse(base_url).netloc
        follow_limit = settings.SCHOOL_SCRAPER_MAX_CANDIDATE_FOLLOW_PAGES

        all_urls: list[str] = []
        method_used = "none"

        # 1. WordPress sitemap
        wp_urls = await self._collect_urls_from_sitemap(f"{base_url}/wp-sitemap.xml")
        if wp_urls:
            all_urls = wp_urls
            method_used = "wp-sitemap"

        # 2. Generic sitemap
        if not all_urls:
            generic_urls = await self._collect_urls_from_sitemap(f"{base_url}/sitemap.xml")
            if generic_urls:
                all_urls = generic_urls
                method_used = "sitemap"

        # 3. robots.txt hint
        if not all_urls:
            robots_sitemap = await self._get_sitemap_url_from_robots(base_url)
            if robots_sitemap:
                robots_urls = await self._collect_urls_from_sitemap(robots_sitemap)
                if robots_urls:
                    all_urls = robots_urls
                    method_used = "robots-txt"

        # 4. Nav-crawl fallback (also supplements when sitemap yields no matches)
        initial_candidates = self._filter_candidates(all_urls, keywords, max_candidates)

        if not all_urls or not initial_candidates:
            nav_urls = await self._collect_urls_from_nav(base_url)
            if nav_urls:
                all_urls = list(dict.fromkeys(all_urls + nav_urls))
                if method_used == "none":
                    method_used = "nav-crawl"
                else:
                    method_used = f"{method_used}+nav-crawl"
            initial_candidates = self._filter_candidates(all_urls, keywords, max_candidates)

        # 5. Follow-up crawl on top candidates to surface deeper sub-pages.
        #    E.g. homepage nav has /school-committee/about but the actual
        #    meeting-archive page lives at /school-committee/meeting-archives.
        if initial_candidates:
            sub_urls = await self._follow_candidate_subpages(
                initial_candidates, base_domain, follow_limit
            )
            if sub_urls:
                all_urls = list(dict.fromkeys(all_urls + sub_urls))

        candidates = self._filter_candidates(all_urls, keywords, max_candidates)

        return {
            "base_url": base_url,
            "discovery_method": method_used,
            "total_urls_scanned": len(all_urls),
            "candidates": candidates,
        }

    # ------------------------------------------------------------------
    # Media extraction helpers
    # ------------------------------------------------------------------

    def _extract_media_from_page(
        self,
        html: str,
        page_url: str,
        video_ext: list[str],
        audio_ext: list[str],
        doc_ext: list[str],
    ) -> tuple[list[dict], list[str]]:
        """
        Parse HTML and return:
          - media_files: list of dicts for each audio/video/document link found
          - sub_pages: same-domain links that are sub-paths of page_url
            (candidates for pagination / year-archive pages)
        """
        soup = BeautifulSoup(html, "html.parser")
        parsed_base = urlparse(page_url)
        base_domain = parsed_base.netloc
        page_prefix = page_url.rstrip("/")

        all_ext = set(video_ext + audio_ext + doc_ext)
        seen_media: set[str] = set()
        media_files: list[dict] = []
        sub_pages: list[str] = []

        _tag_attrs = [
            ("a", "href"),
            ("source", "src"),
            ("video", "src"),
            ("audio", "src"),
        ]

        for tag_name, attr in _tag_attrs:
            for elem in soup.find_all(tag_name, **{attr: True}):
                raw = str(elem[attr]).strip()
                if not raw or raw.startswith(("#", "mailto:", "tel:")):
                    continue

                full_url = urljoin(page_url, raw)
                parsed = urlparse(full_url)
                path_lower = parsed.path.lower()

                # Check for a matching file extension
                matched_ext = next((e for e in all_ext if path_lower.endswith(e)), None)

                if matched_ext and full_url not in seen_media:
                    seen_media.add(full_url)

                    if matched_ext in video_ext:
                        media_type = "video"
                    elif matched_ext in audio_ext:
                        media_type = "audio"
                    else:
                        media_type = "document"

                    # Best-effort name: <a> link text, then filename from path
                    name: str | None = None
                    if tag_name == "a":
                        text = elem.get_text(strip=True)
                        if text:
                            name = text
                    if not name:
                        filename = parsed.path.split("/")[-1]
                        name = filename if filename else None

                    media_files.append(
                        {
                            "name": name,
                            "url": full_url,
                            "file_extension": matched_ext,
                            "media_type": media_type,
                            "size_bytes": None,
                            "source_page_url": page_url,
                        }
                    )

                elif tag_name == "a" and not matched_ext:
                    # Collect same-domain sub-paths for depth crawling
                    if (
                        parsed.netloc == base_domain
                        and full_url.startswith(page_prefix)
                        and full_url != page_url
                    ):
                        sub_pages.append(full_url)

        return media_files, list(dict.fromkeys(sub_pages))  # deduplicate sub_pages

    # ------------------------------------------------------------------
    # Public API — media scraping
    # ------------------------------------------------------------------

    async def scrape_media_files(
        self,
        page_url: str,
        crawl_depth: int = 1,
    ) -> dict:
        """
        Scrape audio, video and document files from a page, following
        same-domain sub-page links up to crawl_depth levels deep.

        Returns a dict with keys: source_url, pages_crawled, media_files.
        """
        video_ext = settings.SCHOOL_SCRAPER_VIDEO_EXTENSIONS
        audio_ext = settings.SCHOOL_SCRAPER_AUDIO_EXTENSIONS
        doc_ext = settings.SCHOOL_SCRAPER_DOCUMENT_EXTENSIONS
        max_pages = settings.SCHOOL_SCRAPER_MAX_PAGES_PER_CRAWL

        all_media: list[dict] = []
        visited: set[str] = {page_url}
        pages_crawled = 0

        # BFS queue: (url, current_depth)
        queue: list[tuple[str, int]] = [(page_url, 0)]

        while queue:
            current_url, depth = queue.pop(0)

            html = await self._fetch_text(current_url)
            if not html:
                continue

            pages_crawled += 1
            media, sub_pages = self._extract_media_from_page(
                html, current_url, video_ext, audio_ext, doc_ext
            )
            all_media.extend(media)

            if depth < crawl_depth:
                for sub_url in sub_pages:
                    if sub_url not in visited and pages_crawled < max_pages:
                        visited.add(sub_url)
                        queue.append((sub_url, depth + 1))

        # Deduplicate by URL while preserving first-seen order
        seen: set[str] = set()
        unique_media: list[dict] = []
        for m in all_media:
            if m["url"] not in seen:
                seen.add(m["url"])
                unique_media.append(m)

        return {
            "source_url": page_url,
            "pages_crawled": pages_crawled,
            "media_files": unique_media,
        }
