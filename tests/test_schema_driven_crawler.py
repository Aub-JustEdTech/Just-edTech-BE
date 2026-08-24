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

Run:
    poetry run pytest tests/test_schema_driven_crawler.py -v
"""

from __future__ import annotations

import textwrap
from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio  # noqa: F401 — ensures plugin is loaded

from app.services.web_scraper.page_schemas import (
    DataPageInfo,
    PossibleRelevantPage,
    RelevantPage,
)
from app.services.web_scraper.schema_driven_crawler import SchemaDrivenCrawler

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

HOMEPAGE_HTML = textwrap.dedent("""\
    <html><head><title>District Home</title></head>
    <body>
      <nav>
        <a href="/school-committee">School Committee</a>
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
        return HOMEPAGE_HTML

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
        return HOMEPAGE_HTML

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

    with patch.object(crawler, "_fetch", return_value=HOMEPAGE_HTML), \
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

    with patch.object(crawler, "_fetch", return_value=HOMEPAGE_HTML), \
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

    with patch.object(crawler, "_fetch", return_value=HOMEPAGE_HTML), \
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
        return HOMEPAGE_HTML

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
        return HOMEPAGE_HTML

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
        return fetch_map.get(url.rstrip("/"))

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

    with patch.object(crawler, "_fetch", return_value=HOMEPAGE_HTML), \
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

    with patch.object(crawler, "_fetch", return_value=HOMEPAGE_HTML), \
         patch.object(crawler, "_collect_seed_frontier", return_value=[]):
        result = await crawler.crawl("https://example.com")

    assert result.pages_crawled == 2
    assert result.max_pages_limit_reached is False
