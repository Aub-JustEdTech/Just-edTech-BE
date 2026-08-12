"""Unit tests for the bulk scraped-media ingestion driver script.

Everything is mocked (CRUD helpers + the Celery task's `.delay`) -- no DB,
no network, no Celery broker. Fixture data is deliberately small: 2
districts (schools) with 3 scraped_media rows total, mirroring a
realistic-but-tiny slice rather than the full 400-district corpus.

Run:
    poetry run pytest tests/test_bulk_ingest_scraped_media.py -v
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from scripts.school_data.bulk_ingest_scraped_media import bulk_ingest

pytestmark = pytest.mark.asyncio


def _scraped_media(id_: int, school_id: int, status: str = "discovered"):
    return SimpleNamespace(
        id=id_,
        school_id=school_id,
        media_type="document",
        source_media_url=f"https://district-{school_id}.example.org/file-{id_}.pdf",
        status=status,
    )


# Two districts: school 1 ("Alpha") has 2 rows, school 2 ("Beta") has 1 row.
DISTRICT_ROWS = [
    _scraped_media(1, school_id=1),
    _scraped_media(2, school_id=1),
    _scraped_media(3, school_id=2),
]


@pytest.fixture
def patched_crud(monkeypatch):
    """Stub out every CRUD/DB call bulk_ingest() touches."""
    import scripts.school_data.bulk_ingest_scraped_media as mod

    monkeypatch.setattr(mod, "AsyncSessionLocal", lambda: _NoDbSession())

    list_mock = AsyncMock(return_value=(DISTRICT_ROWS, len(DISTRICT_ROWS)))
    monkeypatch.setattr(mod, "list_scraped_media", list_mock)

    count_mock = AsyncMock(return_value={"discovered": len(DISTRICT_ROWS)})
    monkeypatch.setattr(mod, "count_scraped_media_by_status", count_mock)

    update_mock = AsyncMock()
    monkeypatch.setattr(mod, "update_scraped_media", update_mock)

    stale_mock = AsyncMock(return_value=[])
    monkeypatch.setattr(mod, "list_stale_in_progress_media", stale_mock)

    return SimpleNamespace(
        list=list_mock, count=count_mock, update=update_mock, stale=stale_mock
    )


class _NoDbSession:
    async def __aenter__(self):
        return None

    async def __aexit__(self, *exc):
        return False


async def test_dry_run_enqueues_nothing(patched_crud, monkeypatch):
    """--dry-run must never touch Celery, even with real rows pending."""
    delay_mock = MagicMock()
    import app.tasks.school_scraper_tasks as tasks_mod

    monkeypatch.setattr(tasks_mod.ingest_scraped_media, "delay", delay_mock)

    stats = await bulk_ingest(
        tenant_id=2,
        status="discovered",
        school_id=None,
        limit=None,
        batch_size=200,
        pause_seconds=0.0,
        reset_stale_minutes=0,
        dry_run=True,
    )

    assert stats["total"] == len(DISTRICT_ROWS)
    assert stats["dry_run"] == len(DISTRICT_ROWS)
    assert stats["enqueued"] == 0
    delay_mock.assert_not_called()


async def test_live_run_enqueues_every_row_once(patched_crud, monkeypatch):
    """Non-dry-run must dispatch exactly one Celery task per row, by id."""
    delay_mock = MagicMock()
    import app.tasks.school_scraper_tasks as tasks_mod

    monkeypatch.setattr(tasks_mod.ingest_scraped_media, "delay", delay_mock)

    stats = await bulk_ingest(
        tenant_id=2,
        status="discovered",
        school_id=None,
        limit=None,
        batch_size=200,
        pause_seconds=0.0,
        reset_stale_minutes=0,
        dry_run=False,
    )

    assert stats["enqueued"] == len(DISTRICT_ROWS)
    dispatched_ids = {call.args[0] for call in delay_mock.call_args_list}
    assert dispatched_ids == {row.id for row in DISTRICT_ROWS}


async def test_limit_caps_total_across_districts(patched_crud, monkeypatch):
    """--limit=2 across 2 districts must stop after 2 rows, not 3."""
    delay_mock = MagicMock()
    import app.tasks.school_scraper_tasks as tasks_mod

    monkeypatch.setattr(tasks_mod.ingest_scraped_media, "delay", delay_mock)
    # Simulate the paginated query respecting the requested page_limit.
    patched_crud.list.side_effect = lambda db, tenant_id, **kw: (
        DISTRICT_ROWS[: kw["limit"]],
        len(DISTRICT_ROWS),
    )

    stats = await bulk_ingest(
        tenant_id=2,
        status="discovered",
        school_id=None,
        limit=2,
        batch_size=200,
        pause_seconds=0.0,
        reset_stale_minutes=0,
        dry_run=False,
    )

    assert stats["total"] == 2
    assert stats["enqueued"] == 2
    assert delay_mock.call_count == 2


async def test_reset_stale_skipped_in_dry_run(patched_crud, monkeypatch):
    """Dry-run must preview stale rows without writing status changes."""
    stale_row = _scraped_media(99, school_id=1, status="downloading")
    patched_crud.stale.return_value = [stale_row]

    import app.tasks.school_scraper_tasks as tasks_mod

    monkeypatch.setattr(tasks_mod.ingest_scraped_media, "delay", MagicMock())

    await bulk_ingest(
        tenant_id=2,
        status="discovered",
        school_id=None,
        limit=None,
        batch_size=200,
        pause_seconds=0.0,
        reset_stale_minutes=60,
        dry_run=True,
    )

    patched_crud.update.assert_not_called()


async def test_reset_stale_writes_back_to_discovered(patched_crud, monkeypatch):
    """Live run must flip each stale row to 'discovered' before dispatching."""
    stale_row = _scraped_media(99, school_id=2, status="ingesting")
    patched_crud.stale.return_value = [stale_row]

    import app.tasks.school_scraper_tasks as tasks_mod

    monkeypatch.setattr(tasks_mod.ingest_scraped_media, "delay", MagicMock())

    await bulk_ingest(
        tenant_id=2,
        status="discovered",
        school_id=None,
        limit=None,
        batch_size=200,
        pause_seconds=0.0,
        reset_stale_minutes=60,
        dry_run=False,
    )

    patched_crud.update.assert_awaited_once_with(None, 99, status="discovered")
