"""Unit tests for the new per-tenant / per-district scraped_media rollups.

`db.execute()` is stubbed to return canned rows, so these never touch a
real database. Fixtures use 3 small districts at most, matching the
"discovered" backlog shape these helpers exist to surface.

Run:
    poetry run pytest tests/test_schools_crud_status_helpers.py -v
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.crud.schools import (
    count_scraped_media_by_status,
    list_stale_in_progress_media,
    scraped_media_status_by_school,
)

pytestmark = pytest.mark.asyncio


def _db_returning(all_rows=None, scalars_all=None):
    """Build a fake AsyncSession whose execute() result supports the exact
    accessor (.all() or .scalars().all()) the function under test calls."""
    db = MagicMock()
    result = MagicMock()
    if all_rows is not None:
        result.all.return_value = all_rows
    if scalars_all is not None:
        result.scalars.return_value.all.return_value = scalars_all
    db.execute = AsyncMock(return_value=result)
    return db


async def test_count_scraped_media_by_status_returns_dict():
    db = _db_returning(all_rows=[("discovered", 5), ("completed", 12), ("failed", 1)])

    counts = await count_scraped_media_by_status(db, tenant_id=2)

    assert counts == {"discovered": 5, "completed": 12, "failed": 1}


async def test_count_scraped_media_by_status_empty_tenant():
    db = _db_returning(all_rows=[])

    counts = await count_scraped_media_by_status(db, tenant_id=2)

    assert counts == {}


async def test_status_by_school_sorted_by_backlog_descending():
    """3 districts: Charlie has the biggest 'discovered' backlog, Alpha has
    none, Beta is in between -- result must be ordered Charlie, Beta, Alpha."""
    rows = [
        (1, "0001", "Alpha", "completed", 10),
        (2, "0002", "Beta", "discovered", 3),
        (2, "0002", "Beta", "completed", 7),
        (3, "0003", "Charlie", "discovered", 9),
        (3, "0003", "Charlie", "failed", 2),
    ]
    db = _db_returning(all_rows=rows)

    result = await scraped_media_status_by_school(db, tenant_id=2)

    assert [r["school_name"] for r in result] == ["Charlie", "Beta", "Alpha"]

    charlie = result[0]
    assert charlie["status_counts"] == {"discovered": 9, "failed": 2}
    assert charlie["total"] == 11

    alpha = result[2]
    assert alpha["status_counts"] == {"completed": 10}
    assert alpha["total"] == 10


async def test_status_by_school_district_with_zero_discovered_ranks_last():
    rows = [
        (1, "0001", "Alpha", "discovered", 4),
        (2, "0002", "Beta", "completed", 20),
    ]
    db = _db_returning(all_rows=rows)

    result = await scraped_media_status_by_school(db, tenant_id=2)

    assert result[0]["school_name"] == "Alpha"
    assert result[1]["school_name"] == "Beta"
    assert result[1]["status_counts"].get("discovered", 0) == 0


async def test_list_stale_in_progress_media_filters_by_cutoff():
    stale_row = SimpleNamespace(
        id=42,
        status="downloading",
        updated_at=datetime.now(timezone.utc) - timedelta(minutes=90),
    )
    db = _db_returning(scalars_all=[stale_row])

    result = await list_stale_in_progress_media(db, tenant_id=2, older_than_minutes=60)

    assert result == [stale_row]
    db.execute.assert_awaited_once()


async def test_list_stale_in_progress_media_no_stale_rows():
    db = _db_returning(scalars_all=[])

    result = await list_stale_in_progress_media(db, tenant_id=2, older_than_minutes=60)

    assert result == []
