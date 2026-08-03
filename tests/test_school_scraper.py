"""
Tests for the generalised school website scraper.

Two test suites:
  1. Unit tests  — all HTTP is mocked; fast, deterministic, CI-safe.
  2. Live tests  — hit 5 real school websites; skip with -m "not live" or
                   set SCHOOL_SCRAPER_LIVE_TESTS=0 in the environment.

Run live tests:
    poetry run pytest tests/test_school_scraper.py -m live -v --tb=short

Run unit tests only (default):
    poetry run pytest tests/test_school_scraper.py -m "not live" -v
"""

import os
import textwrap
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
import pytest_asyncio  # noqa: F401 — ensures plugin is loaded

from app.services.web_scraper.school_scraper_service import SchoolScraperService

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

SITEMAP_INDEX_XML = textwrap.dedent("""\
    <?xml version="1.0" encoding="UTF-8"?>
    <sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
      <sitemap><loc>https://example.com/page-sitemap.xml</loc></sitemap>
      <sitemap><loc>https://example.com/post-sitemap.xml</loc></sitemap>
    </sitemapindex>
""")

PAGE_SITEMAP_XML = textwrap.dedent("""\
    <?xml version="1.0" encoding="UTF-8"?>
    <urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
      <url><loc>https://example.com/about/</loc></url>
      <url><loc>https://example.com/meeting-archives/</loc></url>
      <url><loc>https://example.com/board-minutes/</loc></url>
      <url><loc>https://example.com/contact/</loc></url>
    </urlset>
""")

POST_SITEMAP_XML = textwrap.dedent("""\
    <?xml version="1.0" encoding="UTF-8"?>
    <urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
      <url><loc>https://example.com/news/post-1/</loc></url>
    </urlset>
""")

GENERIC_SITEMAP_XML = textwrap.dedent("""\
    <?xml version="1.0" encoding="UTF-8"?>
    <urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
      <url><loc>https://example.com/</loc></url>
      <url><loc>https://example.com/agenda-2024/</loc></url>
    </urlset>
""")

ROBOTS_TXT = textwrap.dedent("""\
    User-agent: *
    Disallow: /wp-admin/

    Sitemap: https://example.com/sitemap.xml
""")

MEETING_PAGE_HTML = textwrap.dedent("""\
    <html><body>
      <h1>Meeting Archives</h1>
      <ul>
        <li><a href="/files/jan2024.mp4">January 2024 Board Meeting</a></li>
        <li><a href="/files/feb2024.mp3">February 2024 Board Meeting</a></li>
        <li><a href="https://cdn.example.com/mar2024.wav">March 2024</a></li>
        <li><a href="/files/budget.pdf">Budget Document</a></li>
        <li><a href="/files/minutes.docx">Meeting Minutes DOCX</a></li>
        <li><a href="/files/attendance.xlsx">Attendance Sheet</a></li>
        <li><a href="/meeting-archives/2023/">2023 Archive</a></li>
      </ul>
    </body></html>
""")

YEAR_PAGE_HTML = textwrap.dedent("""\
    <html><body>
      <a href="/files/dec2023.mp4">December 2023</a>
      <a href="/files/nov2023.mov">November 2023</a>
    </body></html>
""")

NAV_PAGE_HTML = textwrap.dedent("""\
    <html><body>
      <nav>
        <a href="/about/">About</a>
        <a href="/board-minutes/">Board Minutes</a>
        <a href="/governance/">Governance</a>
      </nav>
    </body></html>
""")


def _make_response(text: str, status: int = 200) -> MagicMock:
    """Build a fake httpx.Response-like mock."""
    resp = MagicMock()
    resp.status_code = status
    resp.text = text
    return resp


# ---------------------------------------------------------------------------
# Unit tests — URL discovery (mocked HTTP)
# ---------------------------------------------------------------------------


class TestParseSitemapXml:
    """Static method tests — no network needed."""

    def test_parse_urlset(self):
        urls, children = SchoolScraperService._parse_sitemap_xml(PAGE_SITEMAP_XML)
        assert "https://example.com/meeting-archives/" in urls
        assert "https://example.com/board-minutes/" in urls
        assert children == []

    def test_parse_sitemapindex(self):
        urls, children = SchoolScraperService._parse_sitemap_xml(SITEMAP_INDEX_XML)
        assert urls == []
        assert "https://example.com/page-sitemap.xml" in children
        assert "https://example.com/post-sitemap.xml" in children

    def test_parse_malformed_xml_returns_empty(self):
        urls, children = SchoolScraperService._parse_sitemap_xml("<<not xml>>")
        assert urls == []
        assert children == []

    def test_parse_empty_string(self):
        urls, children = SchoolScraperService._parse_sitemap_xml("")
        assert urls == []
        assert children == []


class TestFilterCandidates:
    """Keyword-filter logic — no network needed."""

    KEYWORDS = ["meeting", "minutes", "board", "archives", "agenda"]

    def test_single_match(self):
        urls = ["https://x.com/about/", "https://x.com/meeting-archives/"]
        result = SchoolScraperService._filter_candidates(urls, self.KEYWORDS, 10)
        assert len(result) == 1
        assert result[0]["url"] == "https://x.com/meeting-archives/"
        assert "meeting" in result[0]["matched_keywords"]
        assert "archives" in result[0]["matched_keywords"]
        assert result[0]["score"] == 2

    def test_ordering_by_score(self):
        urls = [
            "https://x.com/board/",              # score 1
            "https://x.com/board-meeting-minutes/",  # score 3
            "https://x.com/agenda/",             # score 1
        ]
        result = SchoolScraperService._filter_candidates(urls, self.KEYWORDS, 10)
        assert result[0]["url"] == "https://x.com/board-meeting-minutes/"

    def test_max_candidates_respected(self):
        urls = [f"https://x.com/meeting-{i}/" for i in range(20)]
        result = SchoolScraperService._filter_candidates(urls, self.KEYWORDS, 5)
        assert len(result) == 5

    def test_no_matches_returns_empty(self):
        urls = ["https://x.com/about/", "https://x.com/contact/"]
        result = SchoolScraperService._filter_candidates(urls, self.KEYWORDS, 10)
        assert result == []

    def test_deduplication(self):
        urls = ["https://x.com/meeting/", "https://x.com/meeting/"]
        result = SchoolScraperService._filter_candidates(urls, self.KEYWORDS, 10)
        assert len(result) == 1

    def test_case_insensitive_path(self):
        """Keywords are lowercased against lowercased path."""
        urls = ["https://x.com/Board-Minutes/"]
        result = SchoolScraperService._filter_candidates(urls, self.KEYWORDS, 10)
        assert len(result) == 1


@pytest.mark.asyncio
class TestDiscoverCandidateUrls:
    """Full discovery flow — HTTP mocked via patch."""

    async def test_wp_sitemap_used_first(self):
        """When wp-sitemap.xml returns a sitemap index, it is parsed recursively."""
        fetch_map = {
            "https://example.com/wp-sitemap.xml": SITEMAP_INDEX_XML,
            "https://example.com/page-sitemap.xml": PAGE_SITEMAP_XML,
            "https://example.com/post-sitemap.xml": POST_SITEMAP_XML,
        }

        async def fake_fetch(url: str) -> str | None:
            return fetch_map.get(url)

        svc = SchoolScraperService()
        with patch.object(svc, "_fetch_text", side_effect=fake_fetch):
            result = await svc.discover_candidate_urls("https://example.com")

        assert result["discovery_method"] == "wp-sitemap"
        assert result["total_urls_scanned"] == 5  # 4 from page-sitemap + 1 from post-sitemap
        urls = [c["url"] for c in result["candidates"]]
        assert "https://example.com/meeting-archives/" in urls
        assert "https://example.com/board-minutes/" in urls

        await svc.close()

    async def test_falls_back_to_generic_sitemap(self):
        """When wp-sitemap.xml is missing, /sitemap.xml is tried next."""
        fetch_map = {
            "https://example.com/sitemap.xml": GENERIC_SITEMAP_XML,
        }

        async def fake_fetch(url: str) -> str | None:
            return fetch_map.get(url)

        svc = SchoolScraperService()
        with patch.object(svc, "_fetch_text", side_effect=fake_fetch):
            result = await svc.discover_candidate_urls("https://example.com")

        assert result["discovery_method"] == "sitemap"
        urls = [c["url"] for c in result["candidates"]]
        assert "https://example.com/agenda-2024/" in urls

        await svc.close()

    async def test_falls_back_to_robots_txt(self):
        """When wp-sitemap and sitemap.xml are both missing, robots.txt is checked.
        Uses a non-standard sitemap URL in robots.txt so it is not hit by the
        generic sitemap.xml probe."""
        custom_robots = "User-agent: *\nSitemap: https://example.com/custom-sitemap.xml\n"
        fetch_map = {
            # both standard sitemap paths return nothing
            # robots.txt points to a custom sitemap path
            "https://example.com/robots.txt": custom_robots,
            "https://example.com/custom-sitemap.xml": GENERIC_SITEMAP_XML,
        }

        async def fake_fetch(url: str) -> str | None:
            return fetch_map.get(url)

        svc = SchoolScraperService()
        with patch.object(svc, "_fetch_text", side_effect=fake_fetch):
            result = await svc.discover_candidate_urls("https://example.com")

        assert "robots-txt" in result["discovery_method"]

        await svc.close()

    async def test_nav_crawl_fallback(self):
        """When no sitemap exists at all, homepage nav links are used."""
        fetch_map = {
            "https://example.com": NAV_PAGE_HTML,
        }

        async def fake_fetch(url: str) -> str | None:
            return fetch_map.get(url)

        svc = SchoolScraperService()
        with patch.object(svc, "_fetch_text", side_effect=fake_fetch):
            result = await svc.discover_candidate_urls("https://example.com")

        assert "nav-crawl" in result["discovery_method"]
        urls = [c["url"] for c in result["candidates"]]
        # /board-minutes/ and /governance/ should match keywords
        assert any("board" in u or "governance" in u for u in urls)

        await svc.close()

    async def test_nav_crawl_supplements_sitemap_with_no_matches(self):
        """Sitemap found but zero keyword matches → nav-crawl runs as supplement."""
        fetch_map = {
            "https://example.com/wp-sitemap.xml": textwrap.dedent("""\
                <?xml version="1.0" encoding="UTF-8"?>
                <urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
                  <url><loc>https://example.com/about/</loc></url>
                  <url><loc>https://example.com/contact/</loc></url>
                </urlset>
            """),
            "https://example.com": NAV_PAGE_HTML,
        }

        async def fake_fetch(url: str) -> str | None:
            return fetch_map.get(url)

        svc = SchoolScraperService()
        with patch.object(svc, "_fetch_text", side_effect=fake_fetch):
            result = await svc.discover_candidate_urls("https://example.com")

        assert "nav-crawl" in result["discovery_method"]

        await svc.close()

    async def test_all_urls_timeout_returns_empty_candidates(self):
        """When every HTTP fetch times out, _fetch_text returns None for each URL.
        Discovery completes gracefully with zero candidates rather than raising."""

        async def fake_fetch(url: str) -> str | None:
            return None  # simulate all fetches timing out / failing

        svc = SchoolScraperService()
        with patch.object(svc, "_fetch_text", side_effect=fake_fetch):
            result = await svc.discover_candidate_urls("https://example.com")

        assert result["candidates"] == []
        assert result["total_urls_scanned"] == 0

        await svc.close()

    async def test_network_error_returns_empty(self):
        """Network errors on individual URLs are swallowed; discovery continues."""
        async def fake_fetch(url: str) -> str | None:
            return None  # all fetches fail

        svc = SchoolScraperService()
        with patch.object(svc, "_fetch_text", side_effect=fake_fetch):
            result = await svc.discover_candidate_urls("https://example.com")

        assert result["candidates"] == []
        assert result["total_urls_scanned"] == 0

        await svc.close()

    async def test_base_url_normalized(self):
        """Base URL without scheme is normalised to https://."""
        fetch_map: dict[str, str] = {}

        async def fake_fetch(url: str) -> str | None:
            return fetch_map.get(url)

        svc = SchoolScraperService()
        with patch.object(svc, "_fetch_text", side_effect=fake_fetch):
            result = await svc.discover_candidate_urls("example.com")

        assert result["base_url"].startswith("https://")

        await svc.close()

    async def test_follow_up_crawl_surfaces_deeper_subpages(self):
        """
        Simulates the BPS pattern: homepage nav has /school-committee/about
        but the real meeting-archive page (/school-committee/meeting-archives)
        is only linked from within /school-committee/about.

        The follow-up crawl should discover and return it as a better candidate.
        """
        homepage_html = textwrap.dedent("""\
            <html><body>
              <nav>
                <a href="/school-committee/about">School Committee</a>
                <a href="/about/">About BPS</a>
              </nav>
            </body></html>
        """)
        section_html = textwrap.dedent("""\
            <html><body>
              <nav>
                <a href="/school-committee/about">About</a>
                <a href="/school-committee/meeting-archives">Meeting Archives</a>
                <a href="/school-committee/meetings">Upcoming Meetings</a>
              </nav>
            </body></html>
        """)
        fetch_map = {
            "https://example.com": homepage_html,
            "https://example.com/school-committee/about": section_html,
        }

        async def fake_fetch(url: str) -> str | None:
            return fetch_map.get(url)

        svc = SchoolScraperService()
        with patch.object(svc, "_fetch_text", side_effect=fake_fetch):
            result = await svc.discover_candidate_urls("https://example.com")

        urls = [c["url"] for c in result["candidates"]]
        # Without follow-up crawl only /school-committee/about would appear
        # (score 1 for "committee"). With it, /school-committee/meeting-archives
        # appears with score 3 (committee + meeting + archives) and tops the list.
        assert "https://example.com/school-committee/meeting-archives" in urls

        await svc.close()

    async def test_max_candidates_honoured(self):
        """max_candidates parameter is respected."""
        many_urls = "\n".join(
            f"<url><loc>https://example.com/meeting-{i}/</loc></url>"
            for i in range(50)
        )
        big_sitemap = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
            + many_urls
            + "</urlset>"
        )
        fetch_map = {"https://example.com/wp-sitemap.xml": big_sitemap}

        async def fake_fetch(url: str) -> str | None:
            return fetch_map.get(url)

        svc = SchoolScraperService()
        with patch.object(svc, "_fetch_text", side_effect=fake_fetch):
            result = await svc.discover_candidate_urls("https://example.com", max_candidates=3)

        assert len(result["candidates"]) <= 3

        await svc.close()


# ---------------------------------------------------------------------------
# Unit tests — media scraping (mocked HTTP)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestScrapeMediaFiles:

    async def test_extracts_video_audio_and_documents(self):
        fetch_map = {"https://example.com/meeting-archives/": MEETING_PAGE_HTML}

        async def fake_fetch(url: str) -> str | None:
            return fetch_map.get(url)

        svc = SchoolScraperService()
        with patch.object(svc, "_fetch_text", side_effect=fake_fetch):
            result = await svc.scrape_media_files(
                "https://example.com/meeting-archives/", crawl_depth=0
            )

        assert result["pages_crawled"] == 1
        extensions = {m["file_extension"] for m in result["media_files"]}
        assert ".mp4" in extensions
        assert ".mp3" in extensions
        assert ".wav" in extensions
        assert ".pdf" in extensions
        assert ".docx" in extensions
        assert ".xlsx" in extensions

        await svc.close()

    async def test_media_type_classification(self):
        fetch_map = {"https://example.com/meeting-archives/": MEETING_PAGE_HTML}

        async def fake_fetch(url: str) -> str | None:
            return fetch_map.get(url)

        svc = SchoolScraperService()
        with patch.object(svc, "_fetch_text", side_effect=fake_fetch):
            result = await svc.scrape_media_files(
                "https://example.com/meeting-archives/", crawl_depth=0
            )

        for m in result["media_files"]:
            if m["file_extension"] in (".mp4", ".mov", ".avi", ".webm", ".m4v", ".mkv"):
                assert m["media_type"] == "video"
            elif m["file_extension"] in (".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac"):
                assert m["media_type"] == "audio"
            elif m["file_extension"] in (".pdf", ".docx", ".doc", ".xlsx", ".xls", ".pptx", ".ppt"):
                assert m["media_type"] == "document"

        await svc.close()

    async def test_link_text_used_as_name(self):
        fetch_map = {"https://example.com/meeting-archives/": MEETING_PAGE_HTML}

        async def fake_fetch(url: str) -> str | None:
            return fetch_map.get(url)

        svc = SchoolScraperService()
        with patch.object(svc, "_fetch_text", side_effect=fake_fetch):
            result = await svc.scrape_media_files(
                "https://example.com/meeting-archives/", crawl_depth=0
            )

        names = {m["name"] for m in result["media_files"]}
        assert "January 2024 Board Meeting" in names

        await svc.close()

    async def test_crawl_depth_follows_subpages(self):
        fetch_map = {
            "https://example.com/meeting-archives/": MEETING_PAGE_HTML,
            "https://example.com/meeting-archives/2023/": YEAR_PAGE_HTML,
        }

        async def fake_fetch(url: str) -> str | None:
            return fetch_map.get(url)

        svc = SchoolScraperService()
        with patch.object(svc, "_fetch_text", side_effect=fake_fetch):
            result = await svc.scrape_media_files(
                "https://example.com/meeting-archives/", crawl_depth=1
            )

        assert result["pages_crawled"] == 2
        extensions = {m["file_extension"] for m in result["media_files"]}
        assert ".mov" in extensions  # from YEAR_PAGE_HTML

        await svc.close()

    async def test_crawl_depth_zero_does_not_follow_subpages(self):
        fetch_map = {
            "https://example.com/meeting-archives/": MEETING_PAGE_HTML,
            "https://example.com/meeting-archives/2023/": YEAR_PAGE_HTML,
        }

        async def fake_fetch(url: str) -> str | None:
            return fetch_map.get(url)

        svc = SchoolScraperService()
        with patch.object(svc, "_fetch_text", side_effect=fake_fetch):
            result = await svc.scrape_media_files(
                "https://example.com/meeting-archives/", crawl_depth=0
            )

        # Only the root page is scraped; no .mov file from YEAR_PAGE_HTML
        assert result["pages_crawled"] == 1
        extensions = {m["file_extension"] for m in result["media_files"]}
        assert ".mov" not in extensions

        await svc.close()

    async def test_deduplication_across_pages(self):
        duplicate_html = textwrap.dedent("""\
            <html><body>
              <a href="/files/jan2024.mp4">Jan</a>
              <a href="/files/jan2024.mp4">Jan duplicate</a>
            </body></html>
        """)
        fetch_map = {"https://example.com/meeting-archives/": duplicate_html}

        async def fake_fetch(url: str) -> str | None:
            return fetch_map.get(url)

        svc = SchoolScraperService()
        with patch.object(svc, "_fetch_text", side_effect=fake_fetch):
            result = await svc.scrape_media_files(
                "https://example.com/meeting-archives/", crawl_depth=0
            )

        assert len(result["media_files"]) == 1

        await svc.close()

    async def test_absolute_external_media_url(self):
        """Absolute URLs on a different domain (e.g. CDN) should still be collected."""
        fetch_map = {"https://example.com/meeting-archives/": MEETING_PAGE_HTML}

        async def fake_fetch(url: str) -> str | None:
            return fetch_map.get(url)

        svc = SchoolScraperService()
        with patch.object(svc, "_fetch_text", side_effect=fake_fetch):
            result = await svc.scrape_media_files(
                "https://example.com/meeting-archives/", crawl_depth=0
            )

        media_urls = [m["url"] for m in result["media_files"]]
        assert any("cdn.example.com" in u for u in media_urls)

        await svc.close()

    async def test_empty_page_returns_no_media(self):
        fetch_map = {
            "https://example.com/empty/": "<html><body><p>Nothing here.</p></body></html>"
        }

        async def fake_fetch(url: str) -> str | None:
            return fetch_map.get(url)

        svc = SchoolScraperService()
        with patch.object(svc, "_fetch_text", side_effect=fake_fetch):
            result = await svc.scrape_media_files("https://example.com/empty/", crawl_depth=0)

        assert result["media_files"] == []
        assert result["pages_crawled"] == 1

        await svc.close()

    async def test_source_page_url_recorded(self):
        fetch_map = {
            "https://example.com/meeting-archives/": MEETING_PAGE_HTML,
            "https://example.com/meeting-archives/2023/": YEAR_PAGE_HTML,
        }

        async def fake_fetch(url: str) -> str | None:
            return fetch_map.get(url)

        svc = SchoolScraperService()
        with patch.object(svc, "_fetch_text", side_effect=fake_fetch):
            result = await svc.scrape_media_files(
                "https://example.com/meeting-archives/", crawl_depth=1
            )

        source_pages = {m["source_page_url"] for m in result["media_files"]}
        assert "https://example.com/meeting-archives/" in source_pages
        assert "https://example.com/meeting-archives/2023/" in source_pages

        await svc.close()


# ---------------------------------------------------------------------------
# Robots.txt parsing — unit test
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_document_extensions_extracted():
    """PDF, DOCX and XLSX links are classified as 'document' media_type."""
    docs_page_html = textwrap.dedent("""\
        <html><body>
          <a href="/files/minutes-jan24.pdf">January Minutes</a>
          <a href="/files/budget-fy24.docx">FY24 Budget</a>
          <a href="/files/attendance.xlsx">Attendance</a>
          <a href="/files/presentation.pptx">Slides</a>
          <a href="/other-page/">Not a file</a>
        </body></html>
    """)

    fetch_map = {"https://example.com/committee/": docs_page_html}

    async def fake_fetch(url: str) -> str | None:
        return fetch_map.get(url)

    svc = SchoolScraperService()
    with patch.object(svc, "_fetch_text", side_effect=fake_fetch):
        result = await svc.scrape_media_files("https://example.com/committee/", crawl_depth=0)

    assert result["pages_crawled"] == 1
    assert len(result["media_files"]) == 4

    types = {m["file_extension"]: m["media_type"] for m in result["media_files"]}
    assert types[".pdf"] == "document"
    assert types[".docx"] == "document"
    assert types[".xlsx"] == "document"
    assert types[".pptx"] == "document"

    # Names come from <a> link text
    names = {m["name"] for m in result["media_files"]}
    assert "January Minutes" in names
    assert "FY24 Budget" in names

    await svc.close()


@pytest.mark.asyncio
async def test_get_sitemap_from_robots():
    async def fake_fetch(url: str) -> str | None:
        if url.endswith("/robots.txt"):
            return ROBOTS_TXT
        return None

    svc = SchoolScraperService()
    with patch.object(svc, "_fetch_text", side_effect=fake_fetch):
        url = await svc._get_sitemap_url_from_robots("https://example.com")

    assert url == "https://example.com/sitemap.xml"
    await svc.close()


@pytest.mark.asyncio
async def test_get_sitemap_from_robots_missing():
    async def fake_fetch(url: str) -> str | None:
        return None

    svc = SchoolScraperService()
    with patch.object(svc, "_fetch_text", side_effect=fake_fetch):
        url = await svc._get_sitemap_url_from_robots("https://example.com")

    assert url is None
    await svc.close()


# ---------------------------------------------------------------------------
# Live integration tests — real HTTP requests against 5 school websites
# ---------------------------------------------------------------------------

LIVE_SITES = [
    pytest.param("https://www.akfcs.org",              id="akfcs.org"),
    pytest.param("https://www.abingtonps.org",          id="abingtonps.org"),
    pytest.param("http://www.abschools.org/",           id="abschools.org"),
    pytest.param("https://www.agawamed.org/",           id="agawamed.org"),
    pytest.param("https://www.bostonpublicschools.org", id="bostonpublicschools.org"),
]

_LIVE_ENABLED = os.environ.get("SCHOOL_SCRAPER_LIVE_TESTS", "1") != "0"

live = pytest.mark.skipif(
    not _LIVE_ENABLED,
    reason="Live tests disabled (set SCHOOL_SCRAPER_LIVE_TESTS=1 to enable)",
)


@live
@pytest.mark.live
@pytest.mark.asyncio
@pytest.mark.parametrize("base_url", LIVE_SITES)
async def test_live_discover(base_url: str):
    """
    Runs the full URL-discovery pipeline against a real school website.

    Assertions are intentionally lenient — we record what was found and
    only fail on hard errors (network down, unexpected exception).
    """
    async with SchoolScraperService(timeout=45) as svc:
        result = await svc.discover_candidate_urls(base_url, max_candidates=10)

    print(f"\n[{base_url}]")
    print(f"  method   : {result['discovery_method']}")
    print(f"  scanned  : {result['total_urls_scanned']} URLs")
    print(f"  candidates ({len(result['candidates'])}):")
    for c in result["candidates"]:
        print(f"    score={c['score']} keywords={c['matched_keywords']}  {c['url']}")

    # If every fetch strategy failed the site is unreachable / broken — xfail gracefully
    if result["discovery_method"] == "none":
        pytest.xfail(
            f"All discovery methods returned nothing for {base_url} — "
            "site is likely unreachable or blocks automated requests "
            f"(scanned {result['total_urls_scanned']} URLs)"
        )

    # BPS-specific: follow-up crawl should now surface meeting-archives
    if "bostonpublicschools.org" in base_url:
        candidate_urls = [c["url"] for c in result["candidates"]]
        assert any("meeting-archives" in u for u in candidate_urls), (
            f"Expected meeting-archives in BPS candidates; got: {candidate_urls}"
        )

    # Structural integrity
    assert isinstance(result["total_urls_scanned"], int)
    assert isinstance(result["candidates"], list)
    for c in result["candidates"]:
        assert "url" in c
        assert "score" in c
        assert isinstance(c["matched_keywords"], list)
        assert c["score"] == len(c["matched_keywords"])


@live
@pytest.mark.live
@pytest.mark.asyncio
@pytest.mark.parametrize("base_url", LIVE_SITES)
async def test_live_scrape_media_on_best_candidate(base_url: str):
    """
    If discover() returns at least one candidate, runs scrape_media on
    the top-scoring candidate URL and prints a report of found media files.

    Skips the scrape step (but does not fail) when discovery yields zero
    candidates — that site may simply not have a meeting-archive page.
    """
    async with SchoolScraperService(timeout=45) as svc:
        discovery = await svc.discover_candidate_urls(base_url, max_candidates=10)

        if not discovery["candidates"]:
            pytest.skip(f"No meeting-archive candidate found for {base_url}")

        best = discovery["candidates"][0]["url"]
        print(f"\n[{base_url}] scraping best candidate: {best}")

        media_result = await svc.scrape_media_files(best, crawl_depth=1)

    print(f"  pages crawled : {media_result['pages_crawled']}")
    print(f"  media files   : {len(media_result['media_files'])}")
    for m in media_result["media_files"][:10]:  # print first 10
        print(f"    [{m['media_type']:5}] {m['file_extension']}  {m['name']}  →  {m['url']}")

    # Structural checks only — sites may legitimately have 0 media files
    assert isinstance(media_result["pages_crawled"], int)
    assert isinstance(media_result["media_files"], list)

    # If the page itself returned a non-200 (403, redirect loop, etc.) pages_crawled == 0
    if media_result["pages_crawled"] == 0:
        pytest.xfail(
            f"Could not fetch the candidate page {best} — "
            "server may require authentication or returned a non-200 response"
        )

    for m in media_result["media_files"]:
        assert m["media_type"] in ("video", "audio", "document")
        assert m["file_extension"].startswith(".")
        assert m["url"].startswith("http")
        assert "source_page_url" in m
