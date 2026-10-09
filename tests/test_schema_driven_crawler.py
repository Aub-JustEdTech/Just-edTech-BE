"""
Unit tests for SchemaDrivenCrawler — the deterministic frontier logic.

All HTTP and LLM calls are mocked. These tests cover:
  - visited-set dedup (no URL fetched twice)
  - confidence_threshold pruning of low-confidence candidate links
  - archival skip (skip_archival=True drops is_archive data_pages)
  - max_pages budget enforcement
  - off-domain link rejection
  - sitemap-seeded frontier (the seed URLs enter the frontier before the LLM loop)
  - one end-to-end crawl with a canned RelevantPage from a mocked classifier
  - structured error_details for fetch / empty-markdown failures

Run:
    poetry run pytest tests/test_schema_driven_crawler.py -v
"""

from __future__ import annotations

import textwrap
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio  # noqa: F401 — ensures plugin is loaded

from app.services.web_scraper.domain_utils import (
    host_allowed,
    is_same_organization,
    registrable_domain,
)
from app.services.web_scraper.page_schemas import (
    DataPageInfo,
    PossibleRelevantPage,
    RelevantPage,
)
from app.services.web_scraper.schema_driven_crawler import (
    FetchAttempt,
    FetchMeta,
    SchemaDrivenCrawler,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

HOMEPAGE_HTML = textwrap.dedent("""\
<html><head><title>District Home</title></head>
<body>
  <nav>
    <a href="/about">About Us</a>
    <a href="/staff">Staff Directory</a>
  </nav>
</body></html>
""")

MEETING_PAGE_HTML = textwrap.dedent("""\
    <html><head><title>Meeting Minutes</title></head>
    <body>
      <h1>Minutes</h1>
      <a href="/minutes/2025.pdf">2025 Minutes PDF</a>
    </body></html>
""")

_OK_META = FetchMeta(stage="httpx", http_status=200)


def _ok_fetch(html: str = HOMEPAGE_HTML):
    """Return a `_fetch` result shaped like the real (html, FetchMeta) tuple."""

    async def _inner(client, url):
        return html, _OK_META

    return _inner


def _page(
    url: str,
    *,
    has_data: bool = True,
    has_data_links: bool = False,
    data_type: str = "board_minutes",
    is_archive: bool = False,
    candidates: list[tuple[str, float]] | None = None,
) -> RelevantPage:
    """Build a canned RelevantPage for the mocked classifier to return."""
    return RelevantPage(
        url=url,
        title="t",
        has_data=has_data,
        has_data_links=has_data_links,
        description=None,
        data_page_info=DataPageInfo(
            data_type=data_type,
            is_archive=is_archive,
            data_years_available=[2025] if is_archive else [],
            confidence=1.0,
        ) if has_data else None,
        possible_relevant_pages=[
            PossibleRelevantPage(url=u, confidence=c, reason=None)
            for u, c in (candidates or [])
        ],
    )


# ---------------------------------------------------------------------------
# Frontier / visited-set / budget / threshold / archival-skip
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_visited_set_dedups_repeated_urls():
    """A URL suggested as a candidate that's already been visited is not re-fetched."""
    crawler = SchemaDrivenCrawler(max_pages=5, confidence_threshold=0.4)
    # First page suggests itself again as a candidate — must not re-fetch.
    page1 = _page("https://example.com/", candidates=[("https://example.com/", 0.9)])
    mock_classifier = MagicMock()
    mock_classifier.classify = AsyncMock(return_value=page1)
    crawler.classifier = mock_classifier

    fetched: list[str] = []

    async def fake_fetch(client, url):
        fetched.append(url)
        return HOMEPAGE_HTML, _OK_META

    with patch.object(crawler, "_fetch", side_effect=fake_fetch), \
         patch.object(crawler, "_collect_seed_frontier", return_value=[]):
        result = await crawler.crawl("https://example.com")

    assert result.pages_crawled == 1
    assert fetched == ["https://example.com"]


@pytest.mark.asyncio
async def test_confidence_threshold_prunes_low_confidence_links():
    """Candidate links below confidence_threshold are not added to the frontier."""
    crawler = SchemaDrivenCrawler(max_pages=5, confidence_threshold=0.6)
    page1 = _page(
        "https://example.com/",
        has_data=False,
        candidates=[
            ("https://example.com/high", 0.9),
            ("https://example.com/low", 0.3),  # below threshold
        ],
    )
    page2 = _page("https://example.com/high")
    mock_classifier = MagicMock()
    mock_classifier.classify = AsyncMock(side_effect=[page1, page2])
    crawler.classifier = mock_classifier

    fetched: list[str] = []

    async def fake_fetch(client, url):
        fetched.append(url)
        return HOMEPAGE_HTML, _OK_META

    with patch.object(crawler, "_fetch", side_effect=fake_fetch), \
         patch.object(crawler, "_collect_seed_frontier", return_value=[]):
        result = await crawler.crawl("https://example.com")

    # /low must never be fetched; /high is.
    assert "https://example.com/low" not in fetched
    assert "https://example.com/high" in fetched
    assert result.pages_crawled == 2


@pytest.mark.asyncio
async def test_archival_skip_drops_archive_from_data_pages():
    """When skip_archival=True, is_archive pages are visited but not in data_pages."""
    crawler = SchemaDrivenCrawler(max_pages=3, skip_archival=True)
    archive_page = _page("https://example.com/archive", is_archive=True)
    mock_classifier = MagicMock()
    mock_classifier.classify = AsyncMock(return_value=archive_page)
    crawler.classifier = mock_classifier

    with patch.object(crawler, "_fetch", side_effect=_ok_fetch()), \
         patch.object(crawler, "_collect_seed_frontier", return_value=[]):
        result = await crawler.crawl("https://example.com/archive")

    assert len(result.visited_pages) == 1
    assert result.visited_pages[0].data_page_info.is_archive is True
    assert result.data_pages == []  # skipped


@pytest.mark.asyncio
async def test_archival_kept_when_skip_disabled():
    """When skip_archival=False, is_archive pages appear in data_pages."""
    crawler = SchemaDrivenCrawler(max_pages=3, skip_archival=False)
    archive_page = _page("https://example.com/archive", is_archive=True)
    mock_classifier = MagicMock()
    mock_classifier.classify = AsyncMock(return_value=archive_page)
    crawler.classifier = mock_classifier

    with patch.object(crawler, "_fetch", side_effect=_ok_fetch()), \
         patch.object(crawler, "_collect_seed_frontier", return_value=[]):
        result = await crawler.crawl("https://example.com/archive")

    assert len(result.data_pages) == 1
    assert result.data_pages[0].data_page_info.is_archive is True


@pytest.mark.asyncio
async def test_max_pages_budget_enforced():
    """The crawl stops once pages_crawled reaches max_pages."""
    crawler = SchemaDrivenCrawler(max_pages=2, confidence_threshold=0.4)
    # Each page suggests a new page, so the frontier never empties — the
    # budget is the only stop condition.
    pages = [
        _page(f"https://example.com/p{i}", candidates=[(f"https://example.com/p{i+1}", 0.9)])
        for i in range(10)
    ]
    mock_classifier = MagicMock()
    mock_classifier.classify = AsyncMock(side_effect=pages)
    crawler.classifier = mock_classifier

    with patch.object(crawler, "_fetch", side_effect=_ok_fetch()), \
         patch.object(crawler, "_collect_seed_frontier", return_value=[]):
        result = await crawler.crawl("https://example.com/p0")

    assert result.pages_crawled == 2


@pytest.mark.asyncio
async def test_off_domain_links_rejected():
    """Candidate links to a different domain are dropped from the frontier."""
    crawler = SchemaDrivenCrawler(max_pages=5, confidence_threshold=0.4)
    page1 = _page(
        "https://example.com/",
        has_data=False,
        candidates=[
            ("https://other.com/minutes", 0.9),  # off-domain
            ("https://example.com/ok", 0.9),
        ],
    )
    page2 = _page("https://example.com/ok")
    mock_classifier = MagicMock()
    mock_classifier.classify = AsyncMock(side_effect=[page1, page2])
    crawler.classifier = mock_classifier

    fetched: list[str] = []

    async def fake_fetch(client, url):
        fetched.append(url)
        return HOMEPAGE_HTML, _OK_META

    with patch.object(crawler, "_fetch", side_effect=fake_fetch), \
         patch.object(crawler, "_collect_seed_frontier", return_value=[]):
        await crawler.crawl("https://example.com")

    assert "https://other.com/minutes" not in fetched
    assert "https://example.com/ok" in fetched


# ---------------------------------------------------------------------------
# Sitemap-seeded frontier
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sitemap_seeds_frontier_before_llm_loop():
    """Seed URLs from _collect_seed_frontier enter the frontier at confidence 0.5."""
    crawler = SchemaDrivenCrawler(max_pages=5, confidence_threshold=0.4)
    page_home = _page("https://example.com/", has_data=False)
    page_minutes = _page("https://example.com/minutes")
    mock_classifier = MagicMock()
    mock_classifier.classify = AsyncMock(side_effect=[page_home, page_minutes])
    crawler.classifier = mock_classifier

    fetched: list[str] = []

    async def fake_fetch(client, url):
        fetched.append(url)
        return HOMEPAGE_HTML, _OK_META

    # Seed frontier returns /minutes — it should be fetched even though the
    # homepage (mocked) suggests no candidate links.
    with patch.object(crawler, "_fetch", side_effect=fake_fetch), \
         patch.object(
             crawler,
             "_collect_seed_frontier",
             return_value=["https://example.com/minutes"],
         ):
        result = await crawler.crawl("https://example.com")

    assert "https://example.com/minutes" in fetched
    # Homepage (conf 1.0) is popped first, then /minutes (conf 0.5).
    assert fetched[0] == "https://example.com"
    assert result.pages_crawled == 2
    assert len(result.data_pages) == 1
    assert result.data_pages[0].url == "https://example.com/minutes"


# ---------------------------------------------------------------------------
# End-to-end with a mocked classifier
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_e2e_crawl_with_canned_relevant_pages():
    """Full crawl: homepage (links only) → minutes page (data) → data_pages."""
    crawler = SchemaDrivenCrawler(max_pages=5, confidence_threshold=0.5, skip_archival=True)
    homepage = _page(
        "https://example.com/",
        has_data=False,
        has_data_links=True,
        candidates=[("https://example.com/minutes", 0.9)],
    )
    minutes = _page(
        "https://example.com/minutes",
        has_data=True,
        has_data_links=False,
        data_type="board_minutes",
        is_archive=False,
    )
    mock_classifier = MagicMock()
    mock_classifier.classify = AsyncMock(side_effect=[homepage, minutes])
    crawler.classifier = mock_classifier

    fetch_map = {
        "https://example.com": HOMEPAGE_HTML,
        "https://example.com/minutes": MEETING_PAGE_HTML,
    }

    async def fake_fetch(client, url):
        return fetch_map.get(url.rstrip("/")), _OK_META

    with patch.object(crawler, "_fetch", side_effect=fake_fetch), \
         patch.object(crawler, "_collect_seed_frontier", return_value=[]):
        result = await crawler.crawl("https://example.com")

    assert result.pages_crawled == 2
    assert result.llm_calls == 2
    assert len(result.data_pages) == 1
    assert result.data_pages[0].url == "https://example.com/minutes"
    assert result.data_pages[0].data_page_info.data_type == "board_minutes"
    assert len(result.visited_pages) == 2


# ---------------------------------------------------------------------------
# max_pages_limit_reached flag
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_max_pages_limit_reached_true_when_budget_cuts_exploration():
    """Flag flips when max_pages is hit while unvisited URLs remain on the frontier."""
    crawler = SchemaDrivenCrawler(max_pages=2, confidence_threshold=0.4)
    # Each page suggests a fresh next page, so after 2 crawls the frontier
    # still holds p2 (unvisited) — budget, not the site, stopped us.
    pages = [
        _page(
            f"https://example.com/p{i}",
            candidates=[(f"https://example.com/p{i+1}", 0.9)],
        )
        for i in range(10)
    ]
    mock_classifier = MagicMock()
    mock_classifier.classify = AsyncMock(side_effect=pages)
    crawler.classifier = mock_classifier

    with patch.object(crawler, "_fetch", side_effect=_ok_fetch()), \
         patch.object(crawler, "_collect_seed_frontier", return_value=[]):
        result = await crawler.crawl("https://example.com/p0")

    assert result.pages_crawled == 2
    assert result.max_pages_limit_reached is True


@pytest.mark.asyncio
async def test_max_pages_limit_reached_false_when_frontier_empties():
    """Flag stays False when the crawl finishes because the frontier ran out."""
    crawler = SchemaDrivenCrawler(max_pages=5, confidence_threshold=0.4)
    # Homepage suggests one data page that suggests nothing — frontier empties
    # well before the 5-page budget.
    home = _page(
        "https://example.com/",
        has_data=False,
        candidates=[("https://example.com/minutes", 0.9)],
    )
    minutes = _page("https://example.com/minutes")
    mock_classifier = MagicMock()
    mock_classifier.classify = AsyncMock(side_effect=[home, minutes])
    crawler.classifier = mock_classifier

    with patch.object(crawler, "_fetch", side_effect=_ok_fetch()), \
         patch.object(crawler, "_collect_seed_frontier", return_value=[]):
        result = await crawler.crawl("https://example.com")

    assert result.pages_crawled == 2
    assert result.max_pages_limit_reached is False


# ---------------------------------------------------------------------------
# Structured error_details
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fetch_failed_records_structured_error_details():
    """fetch_failed captures http_status / exception / stage, not just the URL."""
    crawler = SchemaDrivenCrawler(max_pages=3, confidence_threshold=0.4)
    crawler.classifier = MagicMock()
    crawler.classifier.classify = AsyncMock()

    fail_meta = FetchMeta(
        stage="playwright",
        http_status=403,
        exception_type=None,
        exception_message=None,
        stages_tried=["httpx", "ua_alt", "playwright"],
    )

    async def fake_fetch(client, url):
        return None, fail_meta

    with patch.object(crawler, "_fetch", side_effect=fake_fetch), \
         patch.object(crawler, "_collect_seed_frontier", return_value=[]):
        result = await crawler.crawl("https://example.com")

    assert result.pages_crawled == 1
    assert result.visited_pages == []
    assert result.llm_calls == 0
    assert len(result.error_details) == 1
    detail = result.error_details[0]
    assert detail.code == "fetch_failed"
    assert detail.url == "https://example.com"
    assert detail.http_status == 403
    assert detail.stage == "playwright"
    assert "status=403" in result.errors[0]
    assert "stage=playwright" in result.errors[0]


@pytest.mark.asyncio
async def test_empty_markdown_records_html_length():
    """empty_markdown includes html_length so operators can spot shell/login pages."""
    crawler = SchemaDrivenCrawler(max_pages=3, confidence_threshold=0.4)
    crawler.classifier = MagicMock()
    crawler.classifier.classify = AsyncMock()

    emptyish = "   \n\t  "

    async def fake_fetch(client, url):
        return emptyish, _OK_META

    with patch.object(crawler, "_fetch", side_effect=fake_fetch), \
         patch.object(crawler, "_collect_seed_frontier", return_value=[]), \
         patch.object(crawler, "_render_markdown", return_value=""):
        result = await crawler.crawl("https://example.com/login")

    assert len(result.error_details) == 1
    detail = result.error_details[0]
    assert detail.code == "empty_markdown"
    assert detail.url == "https://example.com/login"
    assert detail.html_length == len(emptyish)
    assert "html_len=" in result.errors[0]


# ---------------------------------------------------------------------------
# Domain helpers (registrable_domain / is_same_organization / host_allowed)
# ---------------------------------------------------------------------------


def test_registrable_domain_naive_etld_plus_one():
    assert registrable_domain("go.svusd.org") == "svusd.org"
    assert registrable_domain("www.district.org") == "district.org"
    assert registrable_domain("district.org") == "district.org"
    assert registrable_domain("example.com") == "example.com"
    assert registrable_domain("") == ""


def test_is_same_organization_subdomain_match():
    # Subdomain vanity vs apex: same organization.
    assert is_same_organization("go.district.org", "www.district.org") is True
    # Different registrable domains: not same organization.
    assert is_same_organization("district.org", "facebook.com") is False
    # Equal hosts: same.
    assert is_same_organization("district.org", "district.org") is True
    # Empty: not same.
    assert is_same_organization("", "district.org") is False


def test_host_allowed_seed_subdomain_and_allowlist():
    seed = "www.district.org"
    assert host_allowed("www.district.org", seed_host=seed) is True
    # Same registrable domain, different subdomain.
    assert host_allowed("go.district.org", seed_host=seed) is True
    # Foreign domain.
    assert host_allowed("facebook.com", seed_host=seed) is False
    # Redirect-discovered host added to the allowlist.
    allowed = {"www.district.org", "realdistrict.com"}
    assert host_allowed("realdistrict.com", seed_host=seed, allowed_hosts=allowed) is True
    assert host_allowed("other.com", seed_host=seed, allowed_hosts=allowed) is False


# ---------------------------------------------------------------------------
# Same-organization subdomain hops
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_same_org_subdomain_link_is_followed():
    """A candidate link to a different subdomain of the same org is visited."""
    crawler = SchemaDrivenCrawler(max_pages=5, confidence_threshold=0.4)
    page1 = _page(
        "https://www.district.org/",
        has_data=False,
        candidates=[
            ("https://go.district.org/minutes", 0.9),  # same org, different subdomain
        ],
    )
    page2 = _page("https://go.district.org/minutes")
    mock_classifier = MagicMock()
    mock_classifier.classify = AsyncMock(side_effect=[page1, page2])
    crawler.classifier = mock_classifier

    fetched: list[str] = []

    async def fake_fetch(client, url):
        fetched.append(url)
        return HOMEPAGE_HTML, _OK_META

    with patch.object(crawler, "_fetch", side_effect=fake_fetch), \
         patch.object(crawler, "_collect_seed_frontier", return_value=[]):
        await crawler.crawl("https://www.district.org")

    assert "https://go.district.org/minutes" in fetched


@pytest.mark.asyncio
async def test_foreign_domain_still_rejected():
    """A candidate link to an unrelated foreign domain is NOT visited."""
    crawler = SchemaDrivenCrawler(max_pages=5, confidence_threshold=0.4)
    page1 = _page(
        "https://www.district.org/",
        has_data=False,
        candidates=[
            ("https://facebook.com/minutes", 0.9),  # foreign domain
            ("https://www.district.org/ok", 0.9),
        ],
    )
    page2 = _page("https://www.district.org/ok")
    mock_classifier = MagicMock()
    mock_classifier.classify = AsyncMock(side_effect=[page1, page2])
    crawler.classifier = mock_classifier

    fetched: list[str] = []

    async def fake_fetch(client, url):
        fetched.append(url)
        return HOMEPAGE_HTML, _OK_META

    with patch.object(crawler, "_fetch", side_effect=fake_fetch), \
         patch.object(crawler, "_collect_seed_frontier", return_value=[]):
        await crawler.crawl("https://www.district.org")

    assert "https://facebook.com/minutes" not in fetched
    assert "https://www.district.org/ok" in fetched


# ---------------------------------------------------------------------------
# Redirect final host allowlisting
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_redirect_final_host_added_to_allowed_hosts():
    """A seed that 301-redirects to a new host allows that host for the crawl."""
    crawler = SchemaDrivenCrawler(max_pages=5, confidence_threshold=0.4)
    # Homepage on the redirect-target host suggests a meeting page on the
    # same (redirect-target) host — both should be fetched even though the
    # seed host differs.
    page_home = _page(
        "https://realdistrict.com/",
        has_data=False,
        candidates=[("https://realdistrict.com/minutes", 0.9)],
    )
    page_minutes = _page("https://realdistrict.com/minutes")
    mock_classifier = MagicMock()
    mock_classifier.classify = AsyncMock(side_effect=[page_home, page_minutes])
    crawler.classifier = mock_classifier

    fetched: list[str] = []

    async def fake_fetch(client, url):
        fetched.append(url)
        # First fetch (the seed) reports a final_url on a different host.
        if url == "https://schoolblocks.com" or url == "https://schoolblocks.com/":
            meta = FetchMeta(stage="httpx", http_status=200, final_url="https://realdistrict.com")
        else:
            meta = _OK_META
        return HOMEPAGE_HTML, meta

    with patch.object(crawler, "_fetch", side_effect=fake_fetch), \
         patch.object(crawler, "_collect_seed_frontier", return_value=[]):
        result = await crawler.crawl("https://schoolblocks.com")

    assert "https://realdistrict.com/minutes" in fetched
    assert result.pages_crawled >= 2


# ---------------------------------------------------------------------------
# Hub-page HTML link harvest
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_hub_harvest_enqueues_meeting_related_links_when_llm_gave_none():
    """A has_data_links hub with no LLM candidates triggers HTML link harvest."""
    crawler = SchemaDrivenCrawler(max_pages=5, confidence_threshold=0.4)
    # Hub page: has_data_links=True, no candidates from the LLM.
    hub = _page(
        "https://example.com/school-committee",
        has_data=False,
        has_data_links=True,
        candidates=[],  # LLM gave nothing
    )
    minutes = _page("https://example.com/agendas/2025")
    mock_classifier = MagicMock()
    mock_classifier.classify = AsyncMock(side_effect=[hub, minutes])
    crawler.classifier = mock_classifier

    hub_html = textwrap.dedent("""\
        <html><body>
          <a href="/agendas/2025">2025 Agendas</a>
          <a href="/staff">Staff Directory</a>
          <a href="/news">News</a>
        </body></html>
    """)

    fetch_map = {
        "https://example.com/school-committee": hub_html,
        "https://example.com/agendas/2025": MEETING_PAGE_HTML,
    }

    async def fake_fetch(client, url):
        return fetch_map.get(url.rstrip("/")), _OK_META

    with patch.object(crawler, "_fetch", side_effect=fake_fetch), \
         patch.object(crawler, "_collect_seed_frontier", return_value=[]):
        result = await crawler.crawl("https://example.com/school-committee")

    # /agendas/2025 (strong keyword match) must be harvested + fetched.
    assert "https://example.com/agendas/2025" in list(fetch_map)
    assert any(
        p.url == "https://example.com/agendas/2025" for p in result.visited_pages
    )
    # Non-meeting links must not be harvested.
    assert all(
        "https://example.com/staff" not in (p.url or "") and
        "https://example.com/news" not in (p.url or "")
        for p in result.visited_pages
    )


@pytest.mark.asyncio
async def test_hub_harvest_dedupes_against_llm_candidates():
    """When the LLM returns enqueueable candidates, the harvest still fires
    but dedupes against URLs already in the frontier — no double-enqueue.
    """
    crawler = SchemaDrivenCrawler(max_pages=5, confidence_threshold=0.4)
    hub = _page(
        "https://example.com/school-committee",
        has_data=False,
        has_data_links=True,
        candidates=[("https://example.com/minutes", 0.9)],  # LLM gave one
    )
    minutes = _page("https://example.com/minutes")
    mock_classifier = MagicMock()
    mock_classifier.classify = AsyncMock(side_effect=[hub, minutes])
    crawler.classifier = mock_classifier

    hub_html = textwrap.dedent("""\
        <html><body>
          <a href="/agendas/2025">2025 Agendas</a>
          <a href="/minutes">Minutes</a>
        </body></html>
    """)

    fetched: list[str] = []

    async def fake_fetch(client, url):
        fetched.append(url)
        if "school-committee" in url:
            return hub_html, _OK_META
        return MEETING_PAGE_HTML, _OK_META

    with patch.object(crawler, "_fetch", side_effect=fake_fetch), \
         patch.object(crawler, "_collect_seed_frontier", return_value=[]):
        result = await crawler.crawl("https://example.com/school-committee")

    # LLM's /minutes candidate is fetched; harvested /agendas/2025 (strong
    # keyword, not in LLM candidates) is ALSO fetched — the harvest adds
    # complementary links, deduped against the LLM candidate (/minutes is
    # already in the frontier, so it's not double-enqueued).
    assert "https://example.com/minutes" in fetched
    assert "https://example.com/agendas/2025" in fetched
    # /minutes must appear only once in visited (not double-fetched).
    assert sum(1 for p in result.visited_pages if p.url == "https://example.com/minutes") == 1


# ---------------------------------------------------------------------------
# HTTP/1.1 fallback on protocol errors
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_http1_fallback_fires_on_http2_protocol_error():
    """When a Playwright fetch fails with ERR_HTTP2_PROTOCOL_ERROR, the HTTP/1.1
    Chromium fallback is tried and its result is returned.
    """
    crawler = SchemaDrivenCrawler(max_pages=3, confidence_threshold=0.4)
    crawler.classifier = MagicMock()
    crawler.classifier.classify = AsyncMock(return_value=_page("https://example.com"))

    call_log: list[str] = []

    http2_attempt = FetchAttempt(
        exception_type="NetError",
        exception_message="net::ERR_HTTP2_PROTOCOL_ERROR",
    )
    http1_attempt = FetchAttempt(html=HOMEPAGE_HTML, http_status=200, final_url="https://example.com")

    async def fake_fetch(client, url):
        return HOMEPAGE_HTML, _OK_META

    async def fake_ensure_playwright():
        crawler._browser = object()  # mark as launched so _fetch_text_rendered is callable

    async def fake_ensure_http1_playwright():
        crawler._http1_browser = object()
        return crawler._http1_browser

    async def fake_rendered(url, *, browser=None):
        call_log.append("http1" if browser is crawler._http1_browser else "default")
        if browser is crawler._http1_browser:
            return http1_attempt
        return http2_attempt

    with patch.object(crawler, "_fetch_httpx", return_value=FetchAttempt(
        exception_type="RemoteProtocolError", exception_message="http2 protocol error"
    )), \
         patch.object(crawler, "_fetch_curl_cffi", return_value=FetchAttempt(
             exception_type="RemoteProtocolError", exception_message="http2 protocol error"
         )), \
         patch.object(crawler, "_ensure_playwright", side_effect=fake_ensure_playwright), \
         patch.object(crawler, "_ensure_http1_playwright", side_effect=fake_ensure_http1_playwright), \
         patch.object(crawler, "_fetch_text_rendered", side_effect=fake_rendered):
        result = await crawler.crawl("https://example.com")

    assert "http1" in call_log
    assert result.pages_crawled == 1
    assert len(result.data_pages) == 1


@pytest.mark.asyncio
async def test_http1_fallback_not_fired_on_generic_404():
    """A genuine HTTP 404 does NOT trigger the HTTP/1.1 fallback."""
    crawler = SchemaDrivenCrawler(max_pages=3, confidence_threshold=0.4)
    crawler.classifier = MagicMock()
    crawler.classifier.classify = AsyncMock()

    http1_called = False

    async def fake_fetch(client, url):
        return None, FetchMeta(stage="httpx", http_status=404)

    async def fake_ensure_http1_playwright():
        nonlocal http1_called
        http1_called = True
        return object()

    with patch.object(crawler, "_fetch", side_effect=fake_fetch), \
         patch.object(crawler, "_ensure_http1_playwright", side_effect=fake_ensure_http1_playwright), \
         patch.object(crawler, "_collect_seed_frontier", return_value=[]):
        result = await crawler.crawl("https://example.com")

    assert http1_called is False
    assert result.pages_crawled == 1
    assert len(result.error_details) == 1
    assert result.error_details[0].http_status == 404


# ---------------------------------------------------------------------------
# HTTP/1 browser wiring (target.new_page) + SSL / board-portal unlocks
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fetch_text_rendered_uses_passed_http1_browser():
    """Regression: HTTP/1 fallback must open pages on the http1 browser,
    not silently ignore ``browser=`` and use ``self._browser``.
    """
    crawler = SchemaDrivenCrawler(max_pages=1)

    default_browser = MagicMock(name="default_browser")
    http1_browser = MagicMock(name="http1_browser")
    crawler._browser = default_browser

    context = MagicMock()
    page = MagicMock()
    page.url = "https://example.com"
    page.goto = AsyncMock()
    page.content = AsyncMock(return_value=HOMEPAGE_HTML)
    page.close = AsyncMock()
    context.new_page = AsyncMock(return_value=page)
    context.close = AsyncMock()
    http1_browser.new_context = AsyncMock(return_value=context)
    default_browser.new_context = AsyncMock()

    attempt = await crawler._fetch_text_rendered(
        "https://example.com", browser=http1_browser
    )

    assert attempt.html == HOMEPAGE_HTML
    http1_browser.new_context.assert_awaited_once()
    assert http1_browser.new_context.await_args.kwargs.get("ignore_https_errors") is True
    default_browser.new_context.assert_not_called()
    context.new_page.assert_awaited_once()


def test_www_apex_alternates():
    from app.services.web_scraper.schema_driven_crawler import _www_apex_alternates

    assert _www_apex_alternates("https://www.hancockschool.org/path") == [
        "https://hancockschool.org/path"
    ]
    assert _www_apex_alternates("https://hancockschool.org/") == [
        "https://www.hancockschool.org/"
    ]


def test_extract_board_platform_links_surfaces_diligent():
    crawler = SchemaDrivenCrawler(max_pages=1)
    html = textwrap.dedent(
        """
        <html><body>
          <a href="https://acushnetschools.community.diligentoneplatform.com/Portal/">
            Board portal
          </a>
          <a href="/school-committee">local</a>
        </body></html>
        """
    )
    with patch(
        "app.services.web_scraper.schema_driven_crawler.is_board_platform_url",
        side_effect=lambda u: "diligentoneplatform.com" in (u or ""),
    ):
        found = crawler._extract_board_platform_links(
            html=html,
            page_url="https://www.acushnetschools.us",
            visited=set(),
            existing_frontier_urls=set(),
        )
    assert len(found) == 1
    assert "diligentoneplatform.com" in found[0][0]
    assert found[0][1] >= 0.9


@pytest.mark.asyncio
async def test_empty_markdown_escalates_to_playwright_for_large_html():
    """Large empty-markdown HTML (JS shell) forces a Playwright re-render."""
    crawler = SchemaDrivenCrawler(max_pages=3, confidence_threshold=0.4)
    crawler.classifier = MagicMock()
    crawler.classifier.classify = AsyncMock(
        return_value=_page("https://example.com")
    )

    shell_html = "<html>" + ("x" * 6000) + "</html>"
    call_count = {"n": 0}

    async def fake_fetch(client, url):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return shell_html, FetchMeta(stage="httpx", http_status=200)

        # After escalation path inside crawl, _fetch isn't called again —
        # escalation uses _fetch_text_rendered directly.
        return shell_html, FetchMeta(stage="httpx", http_status=200)

    rendered_html = HOMEPAGE_HTML

    async def fake_ensure_playwright():
        crawler._browser = object()

    async def fake_rendered(url, *, browser=None):
        return FetchAttempt(
            html=rendered_html, http_status=200, final_url=url
        )

    with patch.object(crawler, "_fetch", side_effect=fake_fetch), \
         patch.object(crawler, "_collect_seed_frontier", return_value=[]), \
         patch.object(crawler, "_render_markdown", side_effect=["", "Meeting minutes"]), \
         patch.object(crawler, "_ensure_playwright", side_effect=fake_ensure_playwright), \
         patch.object(crawler, "_fetch_text_rendered", side_effect=fake_rendered):
        result = await crawler.crawl("https://example.com")

    assert result.llm_calls == 1
    assert len(result.data_pages) == 1

