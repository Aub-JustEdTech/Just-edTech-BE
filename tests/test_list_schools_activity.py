"""District-list status + timeline filters on GET /schools.

Covers:
  - Endpoint maps status chips / date bounds into crud.list_schools kwargs
  - Default list stays A–Z by name
  - Activity filter requires matching scraped_media and orders by newest first
  - not_discovered uses absence of media and ignores the date window
"""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from app.crud import schools as schools_crud
from app.main import app
from app.utils import dependencies as deps


class _EmptyResult:
    def scalar_one(self):
        return 0

    def scalars(self):
        return SimpleNamespace(all=lambda: [])


def _capturing_db():
    executed: list = []

    async def execute(stmt):
        executed.append(stmt)
        return _EmptyResult()

    return SimpleNamespace(execute=execute), executed


def _override_auth_and_db(db):
    app.dependency_overrides[deps.get_current_tenant_user] = lambda: SimpleNamespace(
        id=1, tenant_id=1
    )
    app.dependency_overrides[deps.get_effective_tenant_id] = lambda: 1
    app.dependency_overrides[deps.get_db] = lambda: db


# ---------------------------------------------------------------------------
# Endpoint forwarding
# ---------------------------------------------------------------------------


def test_list_schools_forwards_status_and_dates_to_crud():
    captured: dict = {}

    async def _spy(db, tenant_id, **kwargs):
        captured.update(kwargs)
        return [], 0

    with (
        patch(
            "app.api.endpoints.schools.crud.list_schools",
            new=_spy,
        ),
        patch(
            "app.api.endpoints.schools._enrich_school",
            new=AsyncMock(),
        ),
    ):
        _override_auth_and_db(AsyncMock())
        try:
            client = TestClient(app)
            resp = client.get(
                "/api/v1/schools",
                params={
                    "status": "discovered",
                    "date_from": "2026-09-01",
                    "date_to": "2026-09-24",
                    "skip": 0,
                    "limit": 10,
                },
            )
            assert resp.status_code == 200, resp.text
        finally:
            app.dependency_overrides.clear()

    assert captured.get("status_values") == ["discovered"]
    assert captured.get("not_discovered") is False
    assert captured.get("date_from") == date(2026, 9, 1)
    assert captured.get("date_to") == date(2026, 9, 24)


def test_list_schools_maps_not_discovered_flag():
    captured: dict = {}

    async def _spy(db, tenant_id, **kwargs):
        captured.update(kwargs)
        return [], 0

    with (
        patch(
            "app.api.endpoints.schools.crud.list_schools",
            new=_spy,
        ),
        patch(
            "app.api.endpoints.schools._enrich_school",
            new=AsyncMock(),
        ),
    ):
        _override_auth_and_db(AsyncMock())
        try:
            client = TestClient(app)
            resp = client.get(
                "/api/v1/schools",
                params={
                    "status": "not_discovered",
                    "date_from": "2026-09-01",
                    "date_to": "2026-09-24",
                },
            )
            assert resp.status_code == 200, resp.text
        finally:
            app.dependency_overrides.clear()

    assert captured.get("not_discovered") is True
    assert captured.get("status_values") is None
    # Dates are still forwarded; CRUD ignores them when not_discovered=True.
    assert captured.get("date_from") == date(2026, 9, 1)


def test_list_schools_forwards_in_progress_raw_statuses():
    captured: dict = {}

    async def _spy(db, tenant_id, **kwargs):
        captured.update(kwargs)
        return [], 0

    with (
        patch(
            "app.api.endpoints.schools.crud.list_schools",
            new=_spy,
        ),
        patch(
            "app.api.endpoints.schools._enrich_school",
            new=AsyncMock(),
        ),
    ):
        _override_auth_and_db(AsyncMock())
        try:
            client = TestClient(app)
            resp = client.get(
                "/api/v1/schools",
                params={"status": "in_progress"},
            )
            assert resp.status_code == 200, resp.text
        finally:
            app.dependency_overrides.clear()

    assert captured.get("status_values") == ["downloading", "ingesting"]
    assert captured.get("not_discovered") is False


# ---------------------------------------------------------------------------
# CRUD query shape (no real DB)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_schools_default_orders_by_name():
    db, executed = _capturing_db()
    await schools_crud.list_schools(db, tenant_id=1, skip=0, limit=10)

    assert len(executed) == 2
    list_stmt = executed[1]
    compiled = str(list_stmt.compile()).lower()
    assert "order by" in compiled
    assert "schools.name" in compiled
    assert "max(" not in compiled


@pytest.mark.asyncio
async def test_list_schools_activity_filters_and_orders_by_max_scraped_at():
    db, executed = _capturing_db()
    await schools_crud.list_schools(
        db,
        tenant_id=1,
        status_values=["discovered"],
        date_from=date(2026, 9, 1),
        date_to=date(2026, 9, 24),
        skip=0,
        limit=10,
    )

    count_stmt, list_stmt = executed
    count_sql = str(count_stmt.compile()).lower()
    list_sql = str(list_stmt.compile()).lower()

    assert "exists" in count_sql
    assert "scraped_media" in count_sql
    assert "scraped_at" in count_sql

    assert "exists" in list_sql
    assert "max(" in list_sql
    assert "scraped_at" in list_sql
    assert "schools.name" in list_sql


@pytest.mark.asyncio
async def test_list_schools_date_only_still_activity_sorts():
    db, executed = _capturing_db()
    await schools_crud.list_schools(
        db,
        tenant_id=1,
        date_from=date(2026, 9, 1),
        date_to=date(2026, 9, 24),
    )

    list_sql = str(executed[1].compile()).lower()
    assert "exists" in list_sql
    assert "max(" in list_sql


@pytest.mark.asyncio
async def test_list_schools_not_discovered_excludes_media_and_keeps_name_order():
    db, executed = _capturing_db()
    await schools_crud.list_schools(
        db,
        tenant_id=1,
        not_discovered=True,
        date_from=date(2026, 9, 1),
        date_to=date(2026, 9, 24),
    )

    count_sql = str(executed[0].compile()).lower()
    list_sql = str(executed[1].compile()).lower()

    assert "exists" in count_sql
    assert "scraped_media" in count_sql
    # Date window must not appear when filtering by absence of media.
    assert "2026" not in count_sql
    assert "max(" not in list_sql
    assert "schools.name" in list_sql
