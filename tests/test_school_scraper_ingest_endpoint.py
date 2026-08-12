"""Tests for the new manual-trigger ingestion endpoint and the per-district
status endpoint added on feat_bulk-scraped-media-ingestion.

Auth and DB are overridden via FastAPI's dependency_overrides (no real JWT,
no real Postgres); CRUD calls and the Celery dispatch are monkeypatched.
Fixture data is 2 small districts / a handful of scraped_media rows.

Run:
    poetry run pytest tests/test_school_scraper_ingest_endpoint.py -v
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.utils.dependencies import (
    get_current_tenant_admin,
    get_current_tenant_user,
    get_db,
)

FAKE_TENANT_ID = 2


def _fake_admin():
    return SimpleNamespace(id=1, tenant_id=FAKE_TENANT_ID, role="tenant_admin")


def _fake_user():
    return SimpleNamespace(id=1, tenant_id=FAKE_TENANT_ID, role="tenant_admin")


async def _fake_db():
    yield MagicMock()


@pytest.fixture
def client():
    # Deliberately not entered as a context manager (matches tests/test_main.py) --
    # the app's lifespan touches real Postgres/Redis, which these unit tests must
    # not require just to exercise a single mocked endpoint.
    app.dependency_overrides[get_current_tenant_admin] = _fake_admin
    app.dependency_overrides[get_current_tenant_user] = _fake_user
    app.dependency_overrides[get_db] = _fake_db
    yield TestClient(app)
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# POST /api/v1/school-scraper/ingest
# ---------------------------------------------------------------------------


def _scraped_media(id_: int, school_id: int):
    return SimpleNamespace(id=id_, school_id=school_id)


class TestIngestEndpointValidation:
    def test_rejects_skipped_year_status(self, client):
        resp = client.post(
            "/api/v1/school-scraper/ingest", json={"status": "skipped_year"}
        )
        assert resp.status_code == 422

    def test_rejects_limit_over_max(self, client):
        resp = client.post("/api/v1/school-scraper/ingest", json={"limit": 5000})
        assert resp.status_code == 422

    def test_rejects_limit_under_min(self, client):
        resp = client.post("/api/v1/school-scraper/ingest", json={"limit": 0})
        assert resp.status_code == 422


class TestIngestEndpointDispatch:
    @pytest.fixture(autouse=True)
    def patched_crud(self, monkeypatch):
        # Two districts worth of rows (school_id 10 and 11), 2 rows total.
        rows = [_scraped_media(101, school_id=10), _scraped_media(102, school_id=11)]

        list_mock = AsyncMock(return_value=(rows, len(rows)))
        count_mock = AsyncMock(return_value={"discovered": len(rows)})
        update_mock = AsyncMock()
        stale_mock = AsyncMock(return_value=[])
        delay_mock = MagicMock()

        monkeypatch.setattr(
            "app.crud.schools.list_scraped_media", list_mock
        )
        monkeypatch.setattr(
            "app.crud.schools.count_scraped_media_by_status", count_mock
        )
        monkeypatch.setattr("app.crud.schools.update_scraped_media", update_mock)
        monkeypatch.setattr(
            "app.crud.schools.list_stale_in_progress_media", stale_mock
        )
        monkeypatch.setattr(
            "app.tasks.school_scraper_tasks.ingest_scraped_media.delay", delay_mock
        )

        self.rows = rows
        self.list_mock = list_mock
        self.count_mock = count_mock
        self.update_mock = update_mock
        self.stale_mock = stale_mock
        self.delay_mock = delay_mock

    def test_dispatches_one_task_per_row_and_reports_count(self, client):
        resp = client.post(
            "/api/v1/school-scraper/ingest",
            json={"status": "discovered", "limit": 200},
        )

        assert resp.status_code == 200
        body = resp.json()  # raw IngestScrapedMediaResponse, no success_response wrapper
        assert body["enqueued"] == len(self.rows)
        assert body["reset_stale"] == 0
        assert self.delay_mock.call_count == len(self.rows)
        dispatched_ids = {c.args[0] for c in self.delay_mock.call_args_list}
        assert dispatched_ids == {row.id for row in self.rows}

    def test_scopes_query_to_callers_tenant_not_client_supplied(self, client):
        client.post("/api/v1/school-scraper/ingest", json={"status": "discovered"})

        # tenant_id must come from current_user, never from the request body.
        _db, tenant_id_arg = self.list_mock.await_args.args
        assert tenant_id_arg == FAKE_TENANT_ID

    def test_reset_stale_minutes_resets_before_dispatch(self, client):
        stale_row = _scraped_media(999, school_id=10)
        self.stale_mock.return_value = [stale_row]

        resp = client.post(
            "/api/v1/school-scraper/ingest",
            json={"status": "discovered", "reset_stale_minutes": 30},
        )

        assert resp.status_code == 200
        assert resp.json()["reset_stale"] == 1
        self.update_mock.assert_awaited_once()


# ---------------------------------------------------------------------------
# GET /api/v1/pipeline/scraped-media/districts
# ---------------------------------------------------------------------------


class TestDistrictStatusEndpoint:
    def test_returns_district_rollup_from_crud_helper(self, client, monkeypatch):
        districts = [
            {
                "school_id": 10,
                "org_code": "0010",
                "school_name": "Alpha",
                "total": 5,
                "status_counts": {"discovered": 5},
            },
            {
                "school_id": 11,
                "org_code": "0011",
                "school_name": "Beta",
                "total": 3,
                "status_counts": {"completed": 3},
            },
        ]
        monkeypatch.setattr(
            "app.api.endpoints.pipeline_status.scraped_media_status_by_school",
            AsyncMock(return_value=districts),
        )

        resp = client.get("/api/v1/pipeline/scraped-media/districts")

        assert resp.status_code == 200
        assert resp.json()["data"]["districts"] == districts
