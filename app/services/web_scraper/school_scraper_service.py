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
from typing import TYPE_CHECKING, Any
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

from app.core.config import settings
from app.services.web_scraper._discovery_helpers import (
    collect_urls_from_nav as _collect_urls_from_nav_helper,
)
from app.services.web_scraper._discovery_helpers import (
    collect_urls_from_sitemap as _collect_urls_from_sitemap_helper,
)
from app.services.web_scraper._discovery_helpers import (
    get_sitemap_url_from_robots as _get_sitemap_url_from_robots_helper,
)
from app.services.web_scraper._discovery_helpers import (
    html_needs_playwright as _html_needs_playwright_helper,
)
from app.services.web_scraper._discovery_helpers import (
    parse_sitemap_xml as _parse_sitemap_xml_helper,
)
from app.services.web_scraper.year_filter import (
    filter_media_files_async,
    should_crawl_page_url,
)

if TYPE_CHECKING:
    from playwright.async_api import Browser, Playwright

logger = logging.getLogger(__name__)

# Matches YouTube URLs anywhere in raw HTML or a JS payload, so embeds hidden
# in lazy-load attributes (data-src) are still discovered. Validation of the
# video ID itself is left to extract_youtube_id.
_YOUTUBE_IN_TEXT_PATTERN = re.compile(
    r"https?://(?:www\.|m\.)?"
    r"(?:youtube(?:-nocookie)?\.com/(?:watch\?[^\s\"'<>\\]*v=[A-Za-z0-9_-]{11}"
    r"|embed/[A-Za-z0-9_-]{11}|v/[A-Za-z0-9_-]{11}"
    r"|live/[A-Za-z0-9_-]{11}|shorts/[A-Za-z0-9_-]{11})"
    r"|youtu\.be/[A-Za-z0-9_-]{11})",
    re.IGNORECASE,
)

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

# _JS_RENDER_SIGNALS and the sitemap/nav/robots helpers now live in
# app/services/web_scraper/_discovery_helpers.py (shared with the schema
# crawler). Re-exported here for any external code that imported the constant
# from this module.


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
        html, _media = await self._fetch_rendered_with_interactions(
            url, wait_until=wait_until, expand_document_folders=False
        )
        return html

    async def _fetch_rendered_with_interactions(
        self,
        url: str,
        *,
        wait_until: str = "load",
        expand_document_folders: bool = True,
    ) -> tuple[str | None, list[dict]]:
        """
        Render ``url`` in Playwright and optionally expand CMS folder widgets.

        Returns ``(html, extra_media)`` where ``extra_media`` are document
        dicts discovered by clicking/opening SharpSchool folder explorers
        (same-URL widgets that never expose PDFs in the initial DOM).

        For board-meeting platform URLs (BoardDocs, Diligent, BoardOnTrack)
        ``wait_until`` is forced to ``networkidle`` (these SPAs inject content
        via XHR after the initial HTML loads) and the HTML of every accessible
        nested iframe is merged into the returned HTML, since these platforms
        render the real meeting/agenda content inside iframes rather than the
        parent document.
        """
        if not self._browser:
            return await self._fetch_text(url), []

        from app.services.web_scraper.board_platforms import (
            board_platform_kind,
            is_board_platform_url,
        )
        from app.services.web_scraper.playwright_interactions import (
            expand_boardontrack_meetings,
            expand_diligent_meetings,
            expand_sharpschool_document_list,
            extract_google_drive_folder_media,
            extract_google_sheets_embed_media,
            looks_like_sharpschool_document_list,
            merge_iframe_content,
        )

        is_board = is_board_platform_url(url)
        effective_wait_until = "networkidle" if is_board else wait_until

        try:
            page = await self._browser.new_page(
                user_agent=settings.SCHOOL_SCRAPER_USER_AGENT
            )
            try:
                await page.goto(
                    url,
                    wait_until=effective_wait_until,
                    timeout=self.timeout * 1000,
                )
                # Sheets embeds need a beat to populate iframe frames; board
                # platforms likewise need a brief settle for any post-load
                # XHR that injects content into iframes.
                try:
                    await page.wait_for_timeout(2500)
                except Exception:  # noqa: BLE001
                    pass
                if is_board:
                    # Merge iframe HTML so the extractor sees the real meeting
                    # content. Falls back to parent-only HTML on cross-origin
                    # frame failures (see merge_iframe_content).
                    html = await merge_iframe_content(page, top_url=url)
                else:
                    html = await page.content()
                extra_media: list[dict] = []

                if expand_document_folders:
                    if looks_like_sharpschool_document_list(html or ""):
                        extra_media.extend(
                            await expand_sharpschool_document_list(
                                page,
                                page_url=url,
                                timeout_ms=self.timeout * 1000,
                            )
                        )
                        try:
                            html = await page.content()
                        except Exception:  # noqa: BLE001
                            pass

                    # Leominster-style Drive folder iframes (list rendered off-page).
                    drive_media = await extract_google_drive_folder_media(
                        html or "",
                        page_url=url,
                        fetch_text=self._fetch_text,
                    )
                    extra_media.extend(drive_media)

                    # Brewster/Nauset-style Google Sheets agenda/minutes tables.
                    sheet_media = await extract_google_sheets_embed_media(
                        page,
                        page_url=url,
                        settle_ms=1000,
                    )
                    extra_media.extend(sheet_media)

                # Dedupe extra media by URL.
                seen: set[str] = set()
                unique_extra: list[dict] = []
                for m in extra_media:
                    u = m.get("url")
                    if not u or u in seen:
                        continue
                    seen.add(u)
                    unique_extra.append(m)

                return html, unique_extra
            finally:
                await page.close()
        except Exception as exc:
            logger.debug(
                "Playwright render failed for %s (%s): %s — falling back to httpx",
                url,
                type(exc).__name__,
                exc,
            )
            return await self._fetch_text(url), []

    @staticmethod
    def _html_needs_playwright(html: str) -> bool:
        """Delegate to the shared helper (kept as a method for backwards compat)."""
        return _html_needs_playwright_helper(html)

    async def _ensure_playwright(self) -> None:
        """
        Lazily launch the Playwright Chromium browser if it is not already
        running.  Safe to call multiple times — subsequent calls are no-ops.
        """
        if self._browser:
            return
        from playwright.async_api import async_playwright

        self._pw = await async_playwright().start()
        self._browser = await self._pw.chromium.launch(**self._chromium_launch_kwargs())
        logger.info(
            "Playwright Chromium auto-launched — JS-rendered navigation detected"
        )

    # ------------------------------------------------------------------
    # Sitemap helpers (delegate to app.services.web_scraper._discovery_helpers)
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_sitemap_xml(xml_text: str) -> tuple[list[str], list[str]]:
        """Delegate to the shared parser (kept as a method for backwards compat)."""
        return _parse_sitemap_xml_helper(xml_text)

    async def _collect_urls_from_sitemap(self, sitemap_url: str) -> list[str]:
        """Delegate to the shared collector, passing the service's httpx fetch."""
        return await _collect_urls_from_sitemap_helper(sitemap_url, self._fetch_text)

    async def _get_sitemap_url_from_robots(self, base_url: str) -> str | None:
        """Delegate to the shared robots parser, passing the service's httpx fetch."""
        return await _get_sitemap_url_from_robots_helper(base_url, self._fetch_text)

    # ------------------------------------------------------------------
    # Nav-crawl fallback (delegate to the shared collector)
    # ------------------------------------------------------------------

    async def _collect_urls_from_nav(self, base_url: str) -> list[str]:
        """Delegate to the shared nav collector with this service's Playwright state."""
        return await _collect_urls_from_nav_helper(
            base_url,
            self._fetch_text,
            fetch_text_rendered=self._fetch_text_rendered,
            ensure_playwright=self._ensure_playwright,
            has_browser=bool(self._browser),
        )

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
                raw.append(
                    {"url": url, "matched_keywords": matched, "score": len(matched)}
                )

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
            generic_urls = await self._collect_urls_from_sitemap(
                f"{base_url}/sitemap.xml"
            )
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
            initial_candidates = self._filter_candidates(
                all_urls, keywords, max_candidates
            )

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

        SharpSchool ``GetFile.ashx`` download endpoints have no file extension
        in the path; treat them as documents (prefer hint, else ``.pdf``).
        """
        matched = next((e for e in all_ext if path_lower.endswith(e)), None)
        if matched:
            return matched
        if filename_hint:
            hint_lower = filename_hint.lower()
            matched = next((e for e in all_ext if hint_lower.endswith(e)), None)
            if matched:
                return matched
        if "getfile.ashx" in path_lower:
            return ".pdf" if ".pdf" in all_ext or not all_ext else next(iter(all_ext))
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
    def _is_granicus_document_url(url: str, link_text: str | None = None) -> bool:
        """
        Detect Granicus platform document URLs that don't have explicit file
        extensions.

        Returns True for:
        - AgendaViewer.php URLs (agenda documents)
        - MinutesViewer.php URLs (minutes documents)
        - GeneratedAgendaViewer.php URLs (generated agenda documents)
        - Cloudfront CDN links that are likely PDFs (used for agenda packets)
        - View.ashx URLs with M=A or M=M parameters (legacy Legistar format)
        """
        url_lower = url.lower()

        # Granicus hosted agenda/minutes viewers
        if any(
            pattern in url_lower
            for pattern in [
                "agendaviewer.php",
                "minutesviewer.php",
                "generatedagendaviewer.php",
            ]
        ):
            return True

        # Legistar View.ashx patterns (M=A for agenda, M=M for minutes)
        if "view.ashx" in url_lower and ("m=a" in url_lower or "m=m" in url_lower):
            return True

        # Cloudfront CDN links that are likely agenda packets (PDFs)
        # Typically hosted at d3n9y02raazwpg.cloudfront.net or similar
        if "cloudfront.net" in url_lower and link_text:
            text_lower = link_text.lower()
            # Check if link text suggests it's a document
            if any(
                kw in text_lower
                for kw in ["agenda", "packet", "minutes", "document"]
            ):
                return True

        return False

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

    async def _append_youtube_media(
        self,
        media_files: list[dict],
        seen_media: set[str],
        *,
        url: str,
        page_url: str,
        name: str | None = None,
    ) -> bool:
        """Append ``url`` as a YouTube item if it is one. Returns True if it was.

        A sibling of ``_append_media_url`` rather than a branch inside it,
        because that method's ``if not matched_ext: return`` is unconditional
        and YouTube URLs have no file extension.

        Dedup is on the CANONICAL url, so the same video linked as
        ``youtu.be/X``, ``/embed/X`` and ``watch?v=X&t=90`` collapses to one
        item — which is what stops us paying three times for one meeting.
        """
        from app.services.transcription.youtube import (
            canonical_youtube_url,
            fetch_youtube_title,
            is_youtube_url,
        )

        canonical = canonical_youtube_url(url)
        if not canonical:
            return False
        if canonical in seen_media:
            return True

        seen_media.add(canonical)
        # Page context (anchor text, iframe title) usually supplies a real
        # name; when it doesn't — or the "name" is just the URL again — fall
        # back to the video's actual title via oEmbed, same as
        # scripts/school_data/run_scrape_districts.py does. Otherwise
        # original_name would just be the raw YouTube link.
        if not name or is_youtube_url(name):
            name = await fetch_youtube_title(canonical) or name
        media_files.append(
            {
                "name": name or canonical,
                "url": canonical,
                # No extension exists for a YouTube video. Downstream
                # consumers must treat file_extension as nullable.
                "file_extension": None,
                "media_type": "youtube",
                "size_bytes": None,
                "source_page_url": page_url,
            }
        )
        return True

    async def _extract_youtube_from_text(
        self,
        text: str,
        page_url: str,
        seen_media: set[str],
        media_files: list[dict],
    ) -> None:
        """Catch YouTube embeds in lazy-load attrs and JS payloads.

        Many CMS themes put the real embed in ``data-src`` and only promote it
        to ``src`` client-side, so the parsed DOM never shows it.
        """
        if not text:
            return
        for match in _YOUTUBE_IN_TEXT_PATTERN.finditer(text):
            await self._append_youtube_media(
                media_files,
                seen_media,
                url=match.group(0),
                page_url=page_url,
            )

    async def _extract_media_urls_from_text(
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

        await self._extract_youtube_from_text(text, page_url, seen_media, media_files)

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

    async def _extract_media_urls_from_json_scripts(
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
                await self._extract_media_urls_from_text(
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

    async def _extract_media_from_page(
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
            # School sites embed board-meeting recordings as YouTube iframes.
            # Without these two, every such video is invisible to discovery.
            ("iframe", "src"),
            ("embed", "src"),
        ]

        for tag_name, attr in _tag_attrs:
            for elem in soup.find_all(tag_name, **{attr: True}):
                raw = str(elem[attr]).strip()
                if not raw or raw.startswith(("#", "mailto:", "tel:")):
                    continue

                full_url = urljoin(page_url, raw)
                parsed = urlparse(full_url)
                path_lower = parsed.path.lower()

                # YouTube URLs carry no file extension, so they must be
                # matched before the extension gate below rejects them.
                #
                # The URL itself never carries a year (youtube.com/watch?v=...),
                # so nearby text is the only cheap source the year filter can
                # infer a date from — link text for <a>, else the iframe/embed's
                # title, else its parent element's text (meeting listings
                # commonly wrap the embed and its date label in one container).
                if tag_name == "a":
                    yt_name = elem.get_text(strip=True) or None
                else:
                    yt_name = elem.get("title") or None
                    if not yt_name and elem.parent:
                        yt_name = elem.parent.get_text(" ", strip=True)[:200] or None

                if await self._append_youtube_media(
                    media_files,
                    seen_media,
                    url=full_url,
                    page_url=page_url,
                    name=yt_name,
                ):
                    continue

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
                    # Check for Granicus document URLs (AgendaViewer.php,
                    # MinutesViewer.php, Cloudfront agenda packets, etc.) that
                    # don't have file extensions in the path.
                    link_text = elem.get_text(strip=True)
                    if self._is_granicus_document_url(full_url, link_text):
                        self._append_media_url(
                            media_files,
                            seen_media,
                            url=full_url,
                            page_url=page_url,
                            all_ext=all_ext,
                            video_ext=video_ext,
                            audio_ext=audio_ext,
                            name=link_text,
                            # Force document type and .pdf extension hint
                            filename_hint="agenda.pdf",
                        )
                    else:
                        # Collect same-domain sub-paths for depth crawling
                        if (
                            parsed.netloc == base_domain
                            and full_url.startswith(page_prefix)
                            and full_url != page_url
                        ):
                            sub_pages.append(full_url)

        # Extract <object data="..."> URLs (common for embedded Granicus calendars)
        from app.services.web_scraper.board_platforms import is_board_platform_url

        for obj_elem in soup.find_all("object", data=True):
            raw = str(obj_elem["data"]).strip()
            if not raw or raw.startswith(("#", "mailto:", "tel:")):
                continue

            full_url = urljoin(page_url, raw)
            parsed = urlparse(full_url)

            # If it's a board platform URL (cross-domain allowed), add as sub-page
            if is_board_platform_url(full_url) and full_url != page_url:
                sub_pages.append(full_url)
            # Or if it's same-domain, add as sub-page
            elif (
                parsed.netloc == base_domain
                and full_url.startswith(page_prefix)
                and full_url != page_url
            ):
                sub_pages.append(full_url)

        # Apptegy / Nuxt and similar CMS platforms embed document URLs in SSR
        # JSON payloads rather than plain <a href="...pdf"> tags.
        await self._extract_media_urls_from_json_scripts(
            soup,
            page_url,
            all_ext,
            video_ext,
            audio_ext,
            seen_media,
            media_files,
        )
        await self._extract_media_urls_from_text(
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
          jQuery after page load) is present in the returned HTML. SharpSchool
          ``#documentList`` folder explorers are expanded automatically so
          documents behind same-URL folder clicks (GetFile.ashx) are collected.
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

        from app.services.web_scraper.board_platforms import (
            board_platform_kind,
            is_board_platform_url,
        )
        from app.services.web_scraper.playwright_interactions import (
            classify_google_url,
            crawl_google_drive_folder,
            expand_boardontrack_meetings,
            expand_diligent_meetings,
            media_from_google_doc,
            media_from_google_drive_file,
        )
        from app.services.transcription.youtube import (
            is_youtube_scrape_url,
            resolve_youtube_media_items,
        )

        all_media: list[dict] = []
        visited: set[str] = {page_url}
        pages_crawled = 0

        # BFS queue: (url, current_depth)
        queue: list[tuple[str, int]] = [(page_url, 0)]

        while queue:
            current_url, depth = queue.pop(0)

            # Google Drive/Docs URLs are handled by dedicated extractors —
            # not the generic HTML link crawler.
            google_kind, google_id = classify_google_url(current_url)
            if google_kind == "folder" and google_id:
                pages_crawled += 1
                drive_media = await crawl_google_drive_folder(
                    google_id,
                    page_url=current_url,
                    fetch_text=self._fetch_text,
                )
                all_media.extend(drive_media)
                continue
            if google_kind in ("file", "open") and google_id:
                pages_crawled += 1
                item = media_from_google_drive_file(
                    google_id,
                    page_url=current_url,
                    allow_unknown_year=True,
                )
                if item:
                    all_media.append(item)
                continue
            if google_kind == "gdoc" and google_id:
                pages_crawled += 1
                item = media_from_google_doc(
                    google_id,
                    page_url=current_url,
                    allow_unknown_year=True,
                )
                if item:
                    all_media.append(item)
                continue

            # Direct YouTube fixed URLs (watch, playlist, channel) — same as
            # Google Drive, not the generic HTML link crawler.
            if is_youtube_scrape_url(current_url):
                pages_crawled += 1
                yt_items = await resolve_youtube_media_items(
                    current_url,
                    source_page_url=page_url,
                )
                if yt_items:
                    all_media.extend(yt_items)
                continue

            extra_media: list[dict] = []

            # Board-meeting platform URLs (BoardDocs, Diligent, BoardOnTrack)
            # are JS/iframe-heavy SPAs — skip the httpx-fingerprint gate and
            # go straight to Playwright + iframe-merge, regardless of whether
            # a browser was already running.
            is_board = is_board_platform_url(current_url)
            if is_board and not self._browser:
                await self._ensure_playwright()

            if self._browser:
                html, extra_media = await self._fetch_rendered_with_interactions(
                    current_url, expand_document_folders=True
                )
                
                # Board platform interaction layers: dispatch to platform-specific
                # meeting/document expanders for in-portal navigation
                max_meetings = getattr(
                    settings, "SCHOOL_SCRAPER_BOARD_PORTAL_MAX_MEETINGS", 24
                )
                timeout_ms = int(settings.WEB_SCRAPER_TIMEOUT_SECONDS * 1000)
                
                if is_board:
                    kind = board_platform_kind(current_url)
                    if kind in ("diligent", "boardontrack"):
                        
                        # Create a new page for the board platform expander
                        page = await self._browser.new_page(
                            user_agent=settings.SCHOOL_SCRAPER_USER_AGENT
                        )
                        try:
                            board_docs: list[dict] = []
                            
                            if kind == "diligent":
                                logger.info(
                                    "Board platform dispatch: expanding Diligent meetings on %s",
                                    current_url,
                                )
                                board_docs = await expand_diligent_meetings(
                                    page,
                                    page_url=current_url,
                                    timeout_ms=timeout_ms,
                                    max_meetings=max_meetings,
                                )
                            elif kind == "boardontrack":
                                logger.info(
                                    "Board platform dispatch: expanding BoardOnTrack meetings on %s",
                                    current_url,
                                )
                                board_docs = await expand_boardontrack_meetings(
                                    page,
                                    page_url=current_url,
                                    timeout_ms=timeout_ms,
                                    max_meetings=max_meetings,
                                )
                            
                            if board_docs:
                                logger.info(
                                    "Board platform dispatch: %s expander returned %d documents for %s",
                                    kind,
                                    len(board_docs),
                                    current_url,
                                )
                                extra_media.extend(board_docs)
                        finally:
                            await page.close()
                
                # Check for Granicus iframe on ANY page with Playwright
                # (many schools embed Granicus in iframes on their own sites)
                try:
                    # Create a temporary page to check for Granicus iframe
                    check_page = await self._browser.new_page(
                        user_agent=settings.SCHOOL_SCRAPER_USER_AGENT
                    )
                    try:
                        await check_page.goto(current_url, wait_until="load", timeout=timeout_ms)
                        await check_page.wait_for_timeout(1500)
                        
                        # Look for Granicus iframe or object embed
                        granicus_iframe_url = await check_page.evaluate(
                            """() => {
                                // Check iframes
                                const iframes = document.querySelectorAll('iframe');
                                for (const iframe of iframes) {
                                    const src = iframe.src || '';
                                    if (src.includes('granicus.com/ViewPublisher')) {
                                        return src;
                                    }
                                }
                                
                                // Check object embeds
                                const objects = document.querySelectorAll('object');
                                for (const obj of objects) {
                                    const data = obj.getAttribute('data') || '';
                                    if (data.includes('granicus.com/ViewPublisher')) {
                                        return data;
                                    }
                                }
                                
                                return null;
                            }"""
                        )
                        
                        if granicus_iframe_url:
                            logger.info(
                                "Board platform dispatch: detected Granicus iframe, expanding meetings from %s",
                                granicus_iframe_url,
                            )
                            from app.services.web_scraper.playwright_interactions import (
                                expand_granicus_meetings,
                            )
                            
                            # Create new page for Granicus iframe
                            granicus_page = await self._browser.new_page(
                                user_agent=settings.SCHOOL_SCRAPER_USER_AGENT
                            )
                            try:
                                granicus_docs = await expand_granicus_meetings(
                                    granicus_page,
                                    page_url=granicus_iframe_url,
                                    timeout_ms=timeout_ms,
                                    max_meetings=max_meetings,
                                )
                                if granicus_docs:
                                    logger.info(
                                        "Board platform dispatch: Granicus expander returned %d documents",
                                        len(granicus_docs),
                                    )
                                    extra_media.extend(granicus_docs)
                            finally:
                                await granicus_page.close()
                    finally:
                        await check_page.close()
                except Exception as exc:  # noqa: BLE001
                    logger.debug(
                        "Granicus iframe detection failed (%s): %s",
                        type(exc).__name__,
                        exc,
                    )
                
            else:
                html = await self._fetch_text(current_url)
                if html and self._html_needs_playwright(html):
                    await self._ensure_playwright()
                    (
                        rendered,
                        extra_media,
                    ) = await self._fetch_rendered_with_interactions(
                        current_url, expand_document_folders=True
                    )
                    if rendered:
                        html = rendered

            if not html and not extra_media:
                continue

            pages_crawled += 1
            if html:
                media, sub_pages = await self._extract_media_from_page(
                    html, current_url, video_ext, audio_ext, doc_ext
                )
                all_media.extend(await filter_media_files_async(media))
            else:
                sub_pages = []

            if extra_media:
                all_media.extend(await filter_media_files_async(extra_media))

            if depth < crawl_depth and not is_board:
                # Board-platform visits are single-page only — don't enqueue
                # their own discovered sub-pages (matches the schema-driven
                # crawler's single-hop scope for these platforms).
                for sub_url in sub_pages:
                    if sub_url not in visited and pages_crawled < max_pages:
                        if not should_crawl_page_url(sub_url):
                            logger.debug(
                                "Skipping out-of-range archive sub-page: %s",
                                sub_url,
                            )
                            continue
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
