"""
Unit tests for sweep_school_media batching + crawl-failure persistence.

Covers:
  - SCHOOL_SCRAPER_SWEEP_MAX_SCHOOLS caps the number of schools per run
  - round-robin ordering (last_scrapped_at ASC NULLS FIRST) rotates schools
  - failed fetch persists last_http_status (None for timeout/network,
    real status for HTTP errors) via record_scrape_result
  - successful fetch persists last_http_status=200
  - SchoolScrapeUrlOut.crawl_failed derives correctly from
    last_scraped_at / last_http_status
  - list_schools(crawl_failed=...) filter narrows correctly

All DB and HTTP calls are mocked. Run:

    poetry run pytest tests/test_sweep_school_batching.py -v
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
import pytest_asyncio  # noqa: F401 -- ensures plugin is loaded

from app.schemas.schools import SchoolScrapeUrlOut

# ---------------------------------------------------------------------------
# SchoolScrapeUrlOut.crawl_failed derivation
# ---------------------------------------------------------------------------


def _url_row(
    *,
    id: int = 1,
    school_id: int = 1,
    last_http_status: int | None = None,
    last_scraped_at: datetime | None = None,
) -> SimpleNamespace:
    """Minimal ORM-like object SchoolScrapeUrlOut.from_attributes can read."""
    return SimpleNamespace(
        id=id,
        school_id=school_id,
        url="https://example.com/minutes",
        crawl_depth=1,
        use_playwright=False,
        confirmed_by_user_id=None,
        confirmed_at=None,
        last_http_status=last_http_status,
        last_crawl_page_count=None,
        last_scraped_at=last_scraped_at,
        is_active=True,
        created_at=datetime(2025, 1, 1, tzinfo=UTC),
        updated_at=datetime(2025, 1, 1, tzinfo=UTC),
    )


def test_crawl_failed_none_when_never_attempted():
    row = _url_row(last_scraped_at=None, last_http_status=None)
    out = SchoolScrapeUrlOut.model_validate(row)
    assert out.crawl_failed is None


def test_crawl_failed_true_after_http_error():
    row = _url_row(
        last_scraped_at=datetime(2025, 9, 10, tzinfo=UTC),
        last_http_status=403,
    )
    out = SchoolScrapeUrlOut.model_validate(row)
    assert out.crawl_failed is True


def test_crawl_failed_true_after_timeout_network():
    # Timeout/network errors persist last_http_status=None but
    # last_scraped_at is set -> still counts as failed.
    row = _url_row(
        last_scraped_at=datetime(2025, 9, 10, tzinfo=UTC),
        last_http_status=None,
    )
    out = SchoolScrapeUrlOut.model_validate(row)
    assert out.crawl_failed is True


def test_crawl_failed_false_after_success():
    row = _url_row(
        last_scraped_at=datetime(2025, 9, 10, tzinfo=UTC),
        last_http_status=200,
    )
    out = SchoolScrapeUrlOut.model_validate(row)
    assert out.crawl_failed is False


def test_crawl_failed_true_after_5xx():
    row = _url_row(
        last_scraped_at=datetime(2025, 9, 10, tzinfo=UTC),
        last_http_status=500,
    )
    out = SchoolScrapeUrlOut.model_validate(row)
    assert out.crawl_failed is True


# ---------------------------------------------------------------------------
# Sweep: rotation + cap + failure persistence
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_db():
    """A fake AsyncSession that returns canned query results.

    The sweep issues three queries:
      1. select(School.id) ordered by last_scrapped_at -- candidate schools
      2. select(SchoolScrapeUrl.school_id).distinct() -- schools with URLs
      3. select(SchoolScrapeUrl) -- the actual rows to crawl

    The fixture lets each test stage (a) the School.id list, (b) the distinct
    school_id set, and (c) the SchoolScrapeUrl rows, then `db.get(School, id)`
    returns a SimpleNamespace school.
    """
    db = AsyncMock()

    # Default: no schools. Tests override via _configure_db below.
    db._schools_by_id: dict[int, SimpleNamespace] = {}

    async def _get(cls, pk):
        return db._schools_by_id.get(pk)

    db.get.side_effect = _get
    return db


def _configure_db(
    db,
    *,
    school_ids_in_order: list[int],
    schools_with_urls: set[int],
    scrape_urls: list[SimpleNamespace],
    schools: dict[int, SimpleNamespace],
):
    """Wire the three queries + db.get() for the sweep."""
    db._schools_by_id = schools

    result_school_ids = MagicMock()
    result_school_ids.scalars.return_value.all.return_value = school_ids_in_order

    result_distinct = MagicMock()
    result_distinct.scalars.return_value.all.return_value = list(
        schools_with_urls
    )

    result_urls = MagicMock()
    result_urls.scalars.return_value.all.return_value = scrape_urls

    async def _execute(stmt):
        # Inspect the compiled statement's columns to route the result.
        compiled = stmt.compile()
        sql = str(compiled).lower()
        if "school_scrape_url" in sql and "distinct" in sql:
            return result_distinct
        if "school_scrape_url" in sql:
            return result_urls
        if "from schools" in sql or "school.id" in sql:
            return result_school_ids
        raise AssertionError(f"unexpected query: {sql}")

    db.execute.side_effect = _execute


def _school(school_id: int, last_scrapped_at=None) -> SimpleNamespace:
    return SimpleNamespace(
        id=school_id,
        tenant_id=1,
        org_code=f"ORG{school_id}",
        name=f"School {school_id}",
        district_type="district",
        website="https://example.com",
        last_scrapped_at=last_scrapped_at,
        is_active=True,
        notes=None,
        created_at=datetime(2025, 1, 1, tzinfo=UTC),
        updated_at=datetime(2025, 1, 1, tzinfo=UTC),
        scrape_urls=[],
        scraped_media_count=0,
    )


def _scrape_url(url_id, school_id, *, url="https://example.com/minutes"):
    return SimpleNamespace(
        id=url_id,
        school_id=school_id,
        url=url,
        crawl_depth=1,
        use_playwright=False,
        is_active=True,
    )


@pytest.mark.asyncio
async def test_sweep_caps_schools_and_picks_never_scraped_first(mock_db):
    """With 3 candidate schools and max_schools=2, only 2 are crawled,
    and never-scraped (last_scrapped_at=None) schools come first."""
    # School 20 was scraped already; 10 and 30 never. Round-robin NULLS FIRST
    # => 10, 30, 20. Cap=2 => crawl 10 and 30.
    schools = {
        10: _school(10, last_scrapped_at=None),
        20: _school(20, last_scrapped_at=datetime(2025, 9, 1, tzinfo=UTC)),
        30: _school(30, last_scrapped_at=None),
    }
    _configure_db(
        mock_db,
        # The query orders by last_scrapped_at NULLS FIRST, then id.
        # NULLS FIRST puts 10 and 30 before 20; id tiebreak puts 10, 30, 20.
        school_ids_in_order=[10, 30, 20],
        schools_with_urls={10, 20, 30},
        scrape_urls=[
            _scrape_url(1, 10),
            _scrape_url(2, 30),
        ],
        schools=schools,
    )

    record_calls = []
    service = AsyncMock()
    service.scrape_media_files.return_value = {
        "pages_crawled": 3,
        "media_files": [],
    }
    service.__aenter__ = AsyncMock(return_value=service)
    service.__aexit__ = AsyncMock(return_value=None)

    async def _record(db, scrape_url, *, http_status, page_count):
        record_calls.append((scrape_url.id, http_status, page_count))

    with (
        patch(
            "app.tasks.school_scraper_tasks.AsyncSessionLocal",
            return_value=_async_ctx(mock_db),
        ),
        patch(
            "app.tasks.school_scraper_tasks.settings.SCHOOL_SCRAPER_SWEEP_MAX_SCHOOLS",
            50,
        ),
        patch(
            "app.crud.schools.record_scrape_result",
            new=_record,
        ),
        patch(
            "app.crud.schools.bulk_create_scraped_media",
            new=AsyncMock(return_value=([], 0)),
        ),
        patch(
            "app.services.web_scraper.school_scraper_service.SchoolScraperService",
            return_value=service,
        ),
        patch("app.tasks.school_scraper_tasks.ingest_scraped_media") as ingest_mock,
    ):
        from app.tasks.school_scraper_tasks import _sweep_school_media_async

        totals = await _sweep_school_media_async(None, 2)

    assert totals["schools"] == 2  # only 2 crawled under the cap
    assert totals["schools_capped"] == 2
    assert totals["schools_remaining"] == 1  # 3 candidates - 2 crawled
    # Never-scraped schools 10 and 30 were crawled (not 20).
    assert {c[0] for c in record_calls} == {1, 2}
    # Both were successful (200) with the right page count.
    assert all(c[1] == 200 and c[2] == 3 for c in record_calls)
    # No media files -> nothing enqueued.
    ingest_mock.delay.assert_not_called()


@pytest.mark.asyncio
async def test_sweep_rotates_on_second_run(mock_db):
    """After a first capped run, the scraped schools move to the back of
    the round-robin queue, so the second run covers different schools."""
    # After run 1 crawled schools 10 and 30 and stamped their
    # last_scrapped_at, the ordering becomes: 20 (None), then 10 and 30 by
    # time. Cap=2 => second run crawls 20 and whichever of 10/30 is older.
    just_scraped = datetime(2025, 9, 10, tzinfo=UTC)
    schools = {
        10: _school(10, last_scrapped_at=just_scraped),
        20: _school(20, last_scrapped_at=None),
        30: _school(30, last_scrapped_at=just_scraped),
    }
    _configure_db(
        mock_db,
        # NULLS FIRST => 20 first, then 10 and 30 (id ascending among equals).
        school_ids_in_order=[20, 10, 30],
        schools_with_urls={10, 20, 30},
        scrape_urls=[_scrape_url(1, 20), _scrape_url(2, 10)],
        schools=schools,
    )

    record_calls = []
    service = AsyncMock()
    service.scrape_media_files.return_value = {
        "pages_crawled": 1,
        "media_files": [],
    }
    service.__aenter__ = AsyncMock(return_value=service)
    service.__aexit__ = AsyncMock(return_value=None)

    async def _record(db, scrape_url, *, http_status, page_count):
        record_calls.append((scrape_url.id, http_status, page_count))

    with (
        patch(
            "app.tasks.school_scraper_tasks.AsyncSessionLocal",
            return_value=_async_ctx(mock_db),
        ),
        patch(
            "app.tasks.school_scraper_tasks.settings.SCHOOL_SCRAPER_SWEEP_MAX_SCHOOLS",
            50,
        ),
        patch("app.crud.schools.record_scrape_result", new=_record),
        patch(
            "app.crud.schools.bulk_create_scraped_media",
            new=AsyncMock(return_value=([], 0)),
        ),
        patch(
            "app.services.web_scraper.school_scraper_service.SchoolScraperService",
            return_value=service,
        ),
    ):
        from app.tasks.school_scraper_tasks import _sweep_school_media_async

        totals = await _sweep_school_media_async(None, 2)

    # School 20 (never scraped) is now first; it was crawled this run.
    assert {c[0] for c in record_calls} == {1, 2}
    assert totals["schools"] == 2
    assert totals["schools_remaining"] == 1


@pytest.mark.asyncio
async def test_sweep_persists_http_error_status(mock_db):
    """An HTTPStatusError from scrape_media_files is persisted with the real
    status code, and the sweep continues."""
    schools = {10: _school(10, last_scrapped_at=None)}
    _configure_db(
        mock_db,
        school_ids_in_order=[10],
        schools_with_urls={10},
        scrape_urls=[_scrape_url(1, 10)],
        schools=schools,
    )

    record_calls = []
    service = AsyncMock()
    # Simulate a 404 from the scraper.
    resp = httpx.Response(status_code=404, request=httpx.Request("GET", "https://example.com/minutes"))
    service.scrape_media_files.side_effect = httpx.HTTPStatusError(
        "Not Found", request=resp.request, response=resp
    )
    service.__aenter__ = AsyncMock(return_value=service)
    service.__aexit__ = AsyncMock(return_value=None)

    async def _record(db, scrape_url, *, http_status, page_count):
        record_calls.append((scrape_url.id, http_status, page_count))

    with (
        patch(
            "app.tasks.school_scraper_tasks.AsyncSessionLocal",
            return_value=_async_ctx(mock_db),
        ),
        patch(
            "app.tasks.school_scraper_tasks.settings.SCHOOL_SCRAPER_SWEEP_MAX_SCHOOLS",
            50,
        ),
        patch("app.crud.schools.record_scrape_result", new=_record),
        patch(
            "app.crud.schools.bulk_create_scraped_media",
            new=AsyncMock(return_value=([], 0)),
        ),
        patch(
            "app.services.web_scraper.school_scraper_service.SchoolScraperService",
            return_value=service,
        ),
    ):
        from app.tasks.school_scraper_tasks import _sweep_school_media_async

        totals = await _sweep_school_media_async(None, 50)

    assert totals["scrape_failures"] == 1
    assert record_calls == [(1, 404, None)]


@pytest.mark.asyncio
async def test_sweep_persists_none_for_timeout_or_network(mock_db):
    """A timeout/network error (non-HTTP) persists last_http_status=None,
    but still stamps last_scraped_at via record_scrape_result so the FE can
    tell 'failed' from 'never crawled'."""
    schools = {10: _school(10, last_scrapped_at=None)}
    _configure_db(
        mock_db,
        school_ids_in_order=[10],
        schools_with_urls={10},
        scrape_urls=[_scrape_url(1, 10)],
        schools=schools,
    )

    record_calls = []
    service = AsyncMock()
    service.scrape_media_files.side_effect = httpx.TimeoutException("timed out")
    service.__aenter__ = AsyncMock(return_value=service)
    service.__aexit__ = AsyncMock(return_value=None)

    async def _record(db, scrape_url, *, http_status, page_count):
        record_calls.append((scrape_url.id, http_status, page_count))

    with (
        patch(
            "app.tasks.school_scraper_tasks.AsyncSessionLocal",
            return_value=_async_ctx(mock_db),
        ),
        patch(
            "app.tasks.school_scraper_tasks.settings.SCHOOL_SCRAPER_SWEEP_MAX_SCHOOLS",
            50,
        ),
        patch("app.crud.schools.record_scrape_result", new=_record),
        patch(
            "app.crud.schools.bulk_create_scraped_media",
            new=AsyncMock(return_value=([], 0)),
        ),
        patch(
            "app.services.web_scraper.school_scraper_service.SchoolScraperService",
            return_value=service,
        ),
    ):
        from app.tasks.school_scraper_tasks import _sweep_school_media_async

        totals = await _sweep_school_media_async(None, 50)

    assert totals["scrape_failures"] == 1
    # Timeout: no HTTP response -> http_status=None, page_count=None.
    assert record_calls == [(1, None, None)]


@pytest.mark.asyncio
async def test_sweep_success_persists_200_and_enqueues_only_created(mock_db):
    """A successful scrape persists last_http_status=200 and enqueues ingest
    ONLY for newly created rows."""
    schools = {10: _school(10, last_scrapped_at=None)}
    _configure_db(
        mock_db,
        school_ids_in_order=[10],
        schools_with_urls={10},
        scrape_urls=[_scrape_url(1, 10)],
        schools=schools,
    )

    record_calls = []
    service = AsyncMock()
    service.scrape_media_files.return_value = {
        "pages_crawled": 5,
        "media_files": [
            {"url": "https://example.com/doc1.pdf", "media_type": "document"},
            {"url": "https://example.com/video1.mp4", "media_type": "video"},
        ],
    }
    service.__aenter__ = AsyncMock(return_value=service)
    service.__aexit__ = AsyncMock(return_value=None)

    async def _record(db, scrape_url, *, http_status, page_count):
        record_calls.append((scrape_url.id, http_status, page_count))

    created_rows = [
        SimpleNamespace(id=101),
        SimpleNamespace(id=102),
    ]

    with (
        patch(
            "app.tasks.school_scraper_tasks.AsyncSessionLocal",
            return_value=_async_ctx(mock_db),
        ),
        patch(
            "app.tasks.school_scraper_tasks.settings.SCHOOL_SCRAPER_SWEEP_MAX_SCHOOLS",
            50,
        ),
        patch("app.crud.schools.record_scrape_result", new=_record),
        patch(
            "app.crud.schools.bulk_create_scraped_media",
            new=AsyncMock(return_value=(created_rows, 0)),
        ),
        patch(
            "app.services.web_scraper.school_scraper_service.SchoolScraperService",
            return_value=service,
        ),
        patch("app.tasks.school_scraper_tasks.ingest_scraped_media") as ingest_mock,
    ):
        from app.tasks.school_scraper_tasks import _sweep_school_media_async

        totals = await _sweep_school_media_async(None, 50)

    assert record_calls == [(1, 200, 5)]
    assert totals["created"] == 2
    assert totals["enqueued"] == 2
    # Only the two created rows were enqueued.
    ingest_mock.delay.assert_any_call(101)
    ingest_mock.delay.assert_any_call(102)
    assert ingest_mock.delay.call_count == 2


@pytest.mark.asyncio
async def test_sweep_zero_cap_means_unlimited(mock_db):
    """max_schools=0 disables the cap -- all candidate schools are crawled."""
    schools = {i: _school(i, last_scrapped_at=None) for i in range(1, 4)}
    _configure_db(
        mock_db,
        school_ids_in_order=[1, 2, 3],
        schools_with_urls={1, 2, 3},
        scrape_urls=[_scrape_url(i, i) for i in range(1, 4)],
        schools=schools,
    )

    service = AsyncMock()
    service.scrape_media_files.return_value = {"pages_crawled": 1, "media_files": []}
    service.__aenter__ = AsyncMock(return_value=service)
    service.__aexit__ = AsyncMock(return_value=None)

    with (
        patch(
            "app.tasks.school_scraper_tasks.AsyncSessionLocal",
            return_value=_async_ctx(mock_db),
        ),
        patch("app.crud.schools.record_scrape_result", new=AsyncMock()),
        patch(
            "app.crud.schools.bulk_create_scraped_media",
            new=AsyncMock(return_value=([], 0)),
        ),
        patch(
            "app.services.web_scraper.school_scraper_service.SchoolScraperService",
            return_value=service,
        ),
    ):
        from app.tasks.school_scraper_tasks import _sweep_school_media_async

        totals = await _sweep_school_media_async(None, 0)

    assert totals["schools"] == 3
    assert totals["schools_remaining"] == 0
    assert totals["schools_capped"] == 3


# ---------------------------------------------------------------------------
# Self-chaining: next 50 enqueued automatically (Option C)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sweep_self_chains_when_schools_remaining(mock_db):
    """When schools_remaining > 0, the sweep enqueues the next batch."""
    schools = {i: _school(i, last_scrapped_at=None) for i in range(1, 6)}
    _configure_db(
        mock_db,
        # 5 candidate schools; cap=2 => 2 crawled, 3 remaining => chain.
        school_ids_in_order=[1, 2, 3, 4, 5],
        schools_with_urls={1, 2, 3, 4, 5},
        scrape_urls=[_scrape_url(1, 1), _scrape_url(2, 2)],
        schools=schools,
    )

    service = AsyncMock()
    service.scrape_media_files.return_value = {"pages_crawled": 1, "media_files": []}
    service.__aenter__ = AsyncMock(return_value=service)
    service.__aexit__ = AsyncMock(return_value=None)

    with (
        patch(
            "app.tasks.school_scraper_tasks.AsyncSessionLocal",
            return_value=_async_ctx(mock_db),
        ),
        patch("app.crud.schools.record_scrape_result", new=AsyncMock()),
        patch(
            "app.crud.schools.bulk_create_scraped_media",
            new=AsyncMock(return_value=([], 0)),
        ),
        patch(
            "app.services.web_scraper.school_scraper_service.SchoolScraperService",
            return_value=service,
        ),
        patch("app.tasks.school_scraper_tasks.ingest_scraped_media"),
        patch("app.tasks.school_scraper_tasks.sweep_school_media") as sweep_mock,
        patch(
            "app.tasks.batch_classification_tasks"
            ".submit_pending_batch_classification_task"
        ) as classify_mock,
    ):
        from app.tasks.school_scraper_tasks import _sweep_school_media_async

        totals = await _sweep_school_media_async(None, 2)

    assert totals["schools_remaining"] == 3
    # The next batch was enqueued with the same args (school_ids, max_schools).
    sweep_mock.delay.assert_called_once_with(school_ids=None, max_schools=2)
    # Each 50-school wave kicks classification for any pending chunks.
    classify_mock.delay.assert_called_once_with()


@pytest.mark.asyncio
async def test_sweep_no_chain_when_all_schools_covered(mock_db):
    """When schools_remaining == 0, no self-chain is enqueued."""
    schools = {1: _school(1, last_scrapped_at=None)}
    _configure_db(
        mock_db,
        school_ids_in_order=[1],
        schools_with_urls={1},
        scrape_urls=[_scrape_url(1, 1)],
        schools=schools,
    )

    service = AsyncMock()
    service.scrape_media_files.return_value = {"pages_crawled": 1, "media_files": []}
    service.__aenter__ = AsyncMock(return_value=service)
    service.__aexit__ = AsyncMock(return_value=None)

    with (
        patch(
            "app.tasks.school_scraper_tasks.AsyncSessionLocal",
            return_value=_async_ctx(mock_db),
        ),
        patch("app.crud.schools.record_scrape_result", new=AsyncMock()),
        patch(
            "app.crud.schools.bulk_create_scraped_media",
            new=AsyncMock(return_value=([], 0)),
        ),
        patch(
            "app.services.web_scraper.school_scraper_service.SchoolScraperService",
            return_value=service,
        ),
        patch("app.tasks.school_scraper_tasks.ingest_scraped_media"),
        patch("app.tasks.school_scraper_tasks.sweep_school_media") as sweep_mock,
        patch(
            "app.tasks.batch_classification_tasks"
            ".submit_pending_batch_classification_task"
        ) as classify_mock,
    ):
        from app.tasks.school_scraper_tasks import _sweep_school_media_async

        totals = await _sweep_school_media_async(None, 50)

    assert totals["schools_remaining"] == 0
    sweep_mock.delay.assert_not_called()
    # Classification still fires after the final wave.
    classify_mock.delay.assert_called_once_with()


# ---------------------------------------------------------------------------
# list_schools crawl_failed filter (query construction only -- no DB)
# ---------------------------------------------------------------------------


def test_list_schools_passes_crawl_failed_to_crud():
    """The /schools endpoint forwards crawl_failed to crud.list_schools."""
    from app.api.endpoints import schools as schools_endpoint

    # Spy on crud.list_schools to capture the crawl_failed kwarg.
    captured: dict = {}

    async def _spy(db, tenant_id, **kwargs):
        captured.update(kwargs)
        return [], 0

    with (
        patch.object(schools_endpoint.crud, "list_schools", new=_spy),
        patch.object(schools_endpoint, "_enrich_school", new=AsyncMock()),
    ):
        # Use sync TestClient to hit the endpoint.
        from fastapi.testclient import TestClient

        from app.main import app

        client = TestClient(app)
        # Bypass auth via a patched dependency.
        from app.utils import dependencies as deps

        app.dependency_overrides[deps.get_current_tenant_user] = lambda: SimpleNamespace(
            id=1, tenant_id=1
        )
        app.dependency_overrides[deps.get_effective_tenant_id] = lambda: 1
        app.dependency_overrides[deps.get_db] = lambda: AsyncMock()
        try:
            resp = client.get(
                "/api/v1/schools?crawl_failed=true&skip=0&limit=10"
            )
            assert resp.status_code == 200, resp.text
        finally:
            app.dependency_overrides.clear()

    assert captured.get("crawl_failed") is True


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


class _async_ctx:
    """Wrap a mock db so `async with AsyncSessionLocal() as db:` yields it."""

    def __init__(self, db):
        self._db = db

    def __call__(self):
        return self

    async def __aenter__(self):
        return self._db

    async def __aexit__(self, *exc):
        return False
