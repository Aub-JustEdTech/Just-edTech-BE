"""
Generalised school website scraper.

Two-step flow:
  1. discover_candidate_urls – finds meeting-minutes-style URLs from a school site
     using sitemaps (WP / generic / robots.txt) with a nav-crawl fallback, then
     a targeted follow-up crawl on the top candidates to surface deeper sub-pages.

     JS auto-detection: the follow-up crawl fetches each candidate with plain
     httpx first.  If the response HTML contains fingerprints of a JS-heavy CMS
     or SPA framework (Finalsite, Next.js, Angular, Nuxt, …), a headless
     Chromium browser is launched automatically via Playwright and the page is
     re-fetched with full JavaScript execution.  This surfaces dynamically-
     injected navigation links (e.g. Finalsite in-section sidebars) that are
     invisible to a plain HTTP client.  The browser is launched at most once per
     request and reused for all remaining candidates.

     Callers can also force Playwright on unconditionally by passing
     use_playwright=True (or setting SCHOOL_SCRAPER_USE_PLAYWRIGHT=true in
     config), which skips the detection step.

  2. scrape_media_files – extracts audio, video and document links from a confirmed
     page, optionally following same-domain sub-pages up to a configurable depth.
"""

import json
import logging
import re
import xml.etree.ElementTree as ET
from typing import TYPE_CHECKING, Any
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

from app.core.config import settings

if TYPE_CHECKING:
    from playwright.async_api import Browser, Playwright

logger = logging.getLogger(__name__)

_SITEMAP_NS = "http://www.sitemaps.org/schemas/sitemap/0.9"

# User-Agent for HTTP requests.
#
# Some school-district sites (notably WordPress installs behind Wordfence /
# Cloudflare-style WAFs, e.g. akfcs.org) block requests whose UA matches
# `python-httpx/<version>`, `Go-http-client`, or generic bot patterns
# (`Mozilla/5.0 (compatible; bot/...)`) with a 403. The same WAFs typically
# allow tool-style UAs such as `curl/<version>` and `okhttp/<version>`.
#
# We therefore default to a curl-style UA, which is the most broadly
# compatible across the school sites we scrape while not pretending to be a
# browser. Override per-deployment via SCHOOL_SCRAPER_USER_AGENT in .env if a
# specific site requires something different.
_DEFAULT_USER_AGENT: str = "curl/8.5.0"

# HTML fingerprints that indicate JavaScript-rendered navigation.
# When any of these strings appear in the raw httpx response body, the
# in-page navigation (sidebars, section menus, etc.) is likely injected by
# client-side JS and httpx alone will miss links.  The follow-up crawl will
# automatically switch to a Playwright browser for the affected site.
_JS_RENDER_SIGNALS: tuple[str, ...] = (
    "finalsitestatic.com",   # Finalsite CMS  (e.g. Boston Public Schools)
    'id="__next"',           # Next.js
    'id="__nuxt"',           # Nuxt.js
    'ng-version="',          # Angular
    "data-reactroot",        # React (legacy attr)
    "_next/static/",         # Next.js static asset path
    "__nuxt_island",         # Nuxt.js islands
    "blackboard.com/",       # Blackboard LMS
    "eschoolsolutions.com",  # eSchool Solutions CMS
    "ccms_documentlinklisting",  # Catapult CMS "Document Link Listing" widget
    "catapultcms.com",       # Catapult CMS (edu2.catapultcms.com utilities)
    "ccms-contentelement",   # Catapult CMS generic content element wrapper
    "apptegy.net",           # Apptegy / Thrillshare CMS (Nuxt SSR)
    "thrillshare.com",       # Apptegy document CDN / API host
)


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
            headers={"User-Agent": settings.SCHOOL_SCRAPER_USER_AGENT},
        )
        self._pw: "Playwright | None" = None
        self._browser: "Browser | None" = None

    @staticmethod
    def _chromium_launch_kwargs() -> dict:
        """
        Build kwargs for `chromium.launch()`.

        When PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH is set (Docker images that
        apt-install a system Chromium instead of Playwright's own downloaded
        browser), point Playwright at that binary and disable the setuid
        sandbox, which isn't usable for a non-root container user.
        """
        kwargs: dict = {"headless": True}
        executable_path = settings.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH
        if executable_path:
            kwargs["executable_path"] = executable_path
            kwargs["args"] = ["--no-sandbox"]
        return kwargs

    async def __aenter__(self) -> "SchoolScraperService":
        if self.use_playwright:
            from playwright.async_api import async_playwright

            self._pw = await async_playwright().start()
            self._browser = await self._pw.chromium.launch(
                **self._chromium_launch_kwargs()
            )
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

    async def _fetch_text_rendered(
        self,
        url: str,
        wait_until: str = "load",
    ) -> str | None:
        """
        Fetch a page's fully JS-rendered HTML using a Playwright browser page.

        Falls back to plain httpx if the browser is not available.

        Uses ``wait_until="load"`` by default so that the page's initial JS
        bundle (navigation, section sidebars, etc.) is fully executed without
        waiting for every background analytics / beacon request to settle.
        'networkidle' is avoided by default because many school district sites
        fire continuous background pings that prevent networkidle from being
        reached within the configured timeout.
        """
        if not self._browser:
            return await self._fetch_text(url)
        try:
            page = await self._browser.new_page()
            try:
                await page.goto(
                    url,
                    wait_until=wait_until,
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

    @staticmethod
    def _html_needs_playwright(html: str) -> bool:
        """
        Return True when the raw HTML contains fingerprints of a JavaScript-heavy
        CMS or SPA framework whose navigation is injected client-side.

        Checked against _JS_RENDER_SIGNALS (case-sensitive substring match —
        the signals are lowercase/mixed-case literals that appear verbatim in
        real pages, so a full lower() pass is unnecessary and avoids false
        positives on content text).
        """
        return any(signal in html for signal in _JS_RENDER_SIGNALS)

    async def _ensure_playwright(self) -> None:
        """
        Lazily launch the Playwright Chromium browser if it is not already
        running.  Safe to call multiple times — subsequent calls are no-ops.
        """
        if self._browser:
            return
        from playwright.async_api import async_playwright

        self._pw = await async_playwright().start()
        self._browser = await self._pw.chromium.launch(
            **self._chromium_launch_kwargs()
        )
        logger.info(
            "Playwright Chromium auto-launched — JS-rendered navigation detected"
        )

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

        Fetch strategy:
        - Browser already available (pre-launched or previously auto-detected):
          use Playwright directly so that JS-rendered navigation menus (e.g.
          Finalsite CMS, Next.js, Angular) are visible in the returned HTML.
        - No browser yet: fetch with plain httpx; if the raw HTML contains known
          JS-framework fingerprints, launch Playwright automatically and re-fetch.
        """
        if self._browser:
            html = await self._fetch_text_rendered(base_url)
        else:
            html = await self._fetch_text(base_url)
            if html and self._html_needs_playwright(html):
                await self._ensure_playwright()
                rendered = await self._fetch_text_rendered(base_url)
                if rendered:
                    html = rendered

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

        Fetch strategy per candidate:
        - Browser already available (pre-launched or previously auto-detected):
          go straight to Playwright for full JS rendering.
        - No browser yet: fetch with plain httpx; if the HTML contains known
          JS-framework / CMS fingerprints (_JS_RENDER_SIGNALS), launch
          Playwright automatically, re-fetch the page, and use the browser
          for all remaining candidates in this loop.

        Returns a flat deduplicated list of discovered URLs.
        """
        extra_urls: list[str] = []
        seen: set[str] = set()

        for candidate in candidates[:max_pages]:
            if self._browser:
                # Playwright is already available (pre-launched via use_playwright=True
                # or auto-detected on a previous candidate). Use it directly — no need
                # for a preliminary httpx fetch.
                html = await self._fetch_text_rendered(candidate["url"])
            else:
                # No browser yet: fetch with fast httpx first.
                html = await self._fetch_text(candidate["url"])
                # Auto-detect JS rendering from the raw HTML.  If signals are
                # found, launch Playwright once and re-fetch this page so that
                # dynamically-injected navigation is present.  All subsequent
                # candidates will also hit the `self._browser` branch above.
                if html and self._html_needs_playwright(html):
                    await self._ensure_playwright()
                    rendered = await self._fetch_text_rendered(candidate["url"])
                    if rendered:
                        html = rendered

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

    @staticmethod
    def _match_file_extension(
        path_lower: str,
        all_ext: set[str],
        *,
        filename_hint: str | None = None,
    ) -> str | None:
        """
        Return the first configured extension that matches ``path_lower`` or,
        when provided, ``filename_hint`` (e.g. Finalsite ``data-file-name``).
        """
        matched = next((e for e in all_ext if path_lower.endswith(e)), None)
        if matched:
            return matched
        if filename_hint:
            hint_lower = filename_hint.lower()
            return next((e for e in all_ext if hint_lower.endswith(e)), None)
        return None

    @staticmethod
    def _media_type_for_extension(
        ext: str,
        video_ext: list[str],
        audio_ext: list[str],
    ) -> str:
        if ext in video_ext:
            return "video"
        if ext in audio_ext:
            return "audio"
        return "document"

    @staticmethod
    def _media_url_pattern(all_ext: set[str]) -> re.Pattern[str]:
        """Regex for absolute http(s) URLs whose path ends in a known media ext."""
        ext_alt = "|".join(
            re.escape(ext.lstrip("."))
            for ext in sorted(all_ext, key=lambda e: len(e.lstrip(".")), reverse=True)
        )
        return re.compile(
            rf"https?://[^\s\"'<>\\]+?\.(?:{ext_alt})(?:\?[^\s\"'<>\\]*)?",
            re.IGNORECASE,
        )

    def _append_media_url(
        self,
        media_files: list[dict],
        seen_media: set[str],
        *,
        url: str,
        page_url: str,
        all_ext: set[str],
        video_ext: list[str],
        audio_ext: list[str],
        name: str | None = None,
        filename_hint: str | None = None,
    ) -> None:
        url = url.rstrip(".,;)]}")
        if not url or url in seen_media:
            return

        path_lower = urlparse(url).path.lower()
        matched_ext = self._match_file_extension(
            path_lower, all_ext, filename_hint=filename_hint
        )
        if not matched_ext:
            return

        seen_media.add(url)
        if not name:
            filename = urlparse(url).path.split("/")[-1]
            name = filename or None

        media_files.append(
            {
                "name": name,
                "url": url,
                "file_extension": matched_ext,
                "media_type": self._media_type_for_extension(
                    matched_ext, video_ext, audio_ext
                ),
                "size_bytes": None,
                "source_page_url": page_url,
            }
        )

    def _extract_media_urls_from_text(
        self,
        text: str,
        page_url: str,
        all_ext: set[str],
        video_ext: list[str],
        audio_ext: list[str],
        seen_media: set[str],
        media_files: list[dict],
    ) -> None:
        """Find absolute media URLs embedded in HTML or JSON script payloads."""
        if not text:
            return

        pattern = self._media_url_pattern(all_ext)
        for match in pattern.finditer(text):
            self._append_media_url(
                media_files,
                seen_media,
                url=match.group(0),
                page_url=page_url,
                all_ext=all_ext,
                video_ext=video_ext,
                audio_ext=audio_ext,
            )

    def _extract_media_urls_from_json_scripts(
        self,
        soup: BeautifulSoup,
        page_url: str,
        all_ext: set[str],
        video_ext: list[str],
        audio_ext: list[str],
        seen_media: set[str],
        media_files: list[dict],
    ) -> None:
        """
        Walk JSON embedded in <script> tags (e.g. Nuxt ``__NUXT_DATA__``).

        Apptegy / Thrillshare document folders SSR their file list into a
        dehydrated JSON tree where URLs are string leaves, not <a href> tags.
        """

        def walk(node: Any) -> None:
            if isinstance(node, str):
                if node.startswith(("http://", "https://")):
                    self._append_media_url(
                        media_files,
                        seen_media,
                        url=node,
                        page_url=page_url,
                        all_ext=all_ext,
                        video_ext=video_ext,
                        audio_ext=audio_ext,
                    )
            elif isinstance(node, dict):
                url = node.get("url")
                if isinstance(url, str) and url.startswith(("http://", "https://")):
                    file_ext = node.get("file_extension")
                    filename_hint: str | None = None
                    if isinstance(file_ext, str):
                        filename_hint = (
                            file_ext if file_ext.startswith(".") else f"file.{file_ext}"
                        )
                    file_name = node.get("file_name") or node.get("name")
                    display_name = (
                        str(file_name) if isinstance(file_name, str) else None
                    )
                    self._append_media_url(
                        media_files,
                        seen_media,
                        url=url,
                        page_url=page_url,
                        all_ext=all_ext,
                        video_ext=video_ext,
                        audio_ext=audio_ext,
                        name=display_name,
                        filename_hint=filename_hint
                        or (display_name if display_name else None),
                    )
                for value in node.values():
                    walk(value)
            elif isinstance(node, list):
                for item in node:
                    walk(item)

        for script in soup.find_all("script"):
            raw = script.string or script.get_text()
            if not raw:
                continue
            script_type = script.get("type", "")
            script_id = (script.get("id") or "").lower()
            if (
                script_type != "application/json"
                and "nuxt" not in script_id
                and not any(
                    marker in raw
                    for marker in (
                        "files-backend",
                        "thrillshare",
                        "file_extension",
                        "foldersAndDocuments",
                    )
                )
            ):
                continue
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError:
                self._extract_media_urls_from_text(
                    raw,
                    page_url,
                    all_ext,
                    video_ext,
                    audio_ext,
                    seen_media,
                    media_files,
                )
                continue
            walk(payload)

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

                filename_hint: str | None = None
                if tag_name == "a":
                    # Finalsite CMS stores the real filename on the anchor while
                    # href points at /fs/resource-manager/view/{uuid} (no ext).
                    filename_hint = elem.get("data-file-name") or elem.get(
                        "data-filename"
                    )

                matched_ext = self._match_file_extension(
                    path_lower, all_ext, filename_hint=filename_hint
                )

                if matched_ext:
                    name: str | None = None
                    if tag_name == "a":
                        text = elem.get_text(strip=True)
                        if text:
                            name = text
                    if not name and filename_hint:
                        name = str(filename_hint)
                    self._append_media_url(
                        media_files,
                        seen_media,
                        url=full_url,
                        page_url=page_url,
                        all_ext=all_ext,
                        video_ext=video_ext,
                        audio_ext=audio_ext,
                        name=name,
                        filename_hint=filename_hint,
                    )

                elif tag_name == "a" and not matched_ext:
                    # Collect same-domain sub-paths for depth crawling
                    if (
                        parsed.netloc == base_domain
                        and full_url.startswith(page_prefix)
                        and full_url != page_url
                    ):
                        sub_pages.append(full_url)

        # Apptegy / Nuxt and similar CMS platforms embed document URLs in SSR
        # JSON payloads rather than plain <a href="...pdf"> tags.
        self._extract_media_urls_from_json_scripts(
            soup,
            page_url,
            all_ext,
            video_ext,
            audio_ext,
            seen_media,
            media_files,
        )
        self._extract_media_urls_from_text(
            html,
            page_url,
            all_ext,
            video_ext,
            audio_ext,
            seen_media,
            media_files,
        )

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

        Fetch strategy per page (same JS auto-detection as discovery):
        - Browser already available (pre-launched via use_playwright=True or
          auto-detected on a previous page in this crawl): fetch with
          Playwright directly so JS-injected content (e.g. Catapult/Finalsite
          CMS document-listing widgets that populate hidden containers via
          jQuery after page load) is present in the returned HTML.
        - No browser yet: fetch with plain httpx first; if the raw HTML
          contains known JS-framework/CMS fingerprints, launch Playwright
          automatically, re-fetch the page, and reuse the browser for all
          remaining pages in the crawl.

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

            if self._browser:
                html = await self._fetch_text_rendered(current_url)
            else:
                html = await self._fetch_text(current_url)
                if html and self._html_needs_playwright(html):
                    await self._ensure_playwright()
                    rendered = await self._fetch_text_rendered(current_url)
                    if rendered:
                        html = rendered

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
