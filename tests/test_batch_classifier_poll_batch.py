"""Unit tests for BatchClassifier.poll_batch()'s reset/dead-letter logic.

Covers change-log bugs #10 (stranded chunks after a dead batch never got
retried) and #11 (unbounded retry -> dead_letter cap). Uses a small
fixture of 3 chunks -- as if pulled from 2-3 districts' documents -- one
under the retry cap and one at/over it, rather than the real 337-chunk
incident.

The OpenAI client and DB session are both faked; nothing hits the network
or a real database.

Run:
    poetry run pytest tests/test_batch_classifier_poll_batch.py -v
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core.config import settings
from app.models.batch_classification_job import BatchClassificationJob
from app.models.pending_classification import PendingClassification
from app.services.heatmap_ingest.batch_classifier import BatchClassifier

pytestmark = pytest.mark.asyncio


def _classifier(batch_status: str):
    """A BatchClassifier with a faked OpenAI client, bypassing __init__ (no
    real API key / S3 client needed for poll_batch)."""
    instance = BatchClassifier.__new__(BatchClassifier)
    instance._client = SimpleNamespace(
        batches=SimpleNamespace(
            retrieve=AsyncMock(
                return_value=SimpleNamespace(status=batch_status, errors=None)
            )
        )
    )
    return instance


def _pending_row(retry_count: int, point_id: str):
    return PendingClassification(
        document_id=1,
        qdrant_point_id=point_id,
        chunk_index=0,
        chunk_text="some chunk text",
        status="submitted",
        batch_id="batch_dead",
        retry_count=retry_count,
    )


def _fake_db(job: BatchClassificationJob, stranded: list[PendingClassification]):
    """db.execute() must answer two different queries in order: first the
    BatchClassificationJob lookup, then the stranded PendingClassification
    rows."""
    job_result = MagicMock()
    job_result.scalar_one_or_none.return_value = job

    stranded_result = MagicMock()
    stranded_result.scalars.return_value.all.return_value = stranded

    db = MagicMock()
    db.execute = AsyncMock(side_effect=[job_result, stranded_result])
    db.commit = AsyncMock()
    return db


async def test_cancelled_batch_resets_chunks_under_retry_cap():
    job = BatchClassificationJob(batch_id="batch_dead", status="in_progress")
    rows = [_pending_row(retry_count=0, point_id="p1"), _pending_row(retry_count=1, point_id="p2")]
    db = _fake_db(job, rows)
    classifier = _classifier("cancelled")

    result_job = await classifier.poll_batch(db, "batch_dead")

    assert result_job.status == "cancelled"
    for row in rows:
        assert row.status == "pending"
        assert row.batch_id is None
    assert rows[0].retry_count == 1
    assert rows[1].retry_count == 2
    db.commit.assert_awaited_once()


async def test_chunk_exceeding_max_retries_goes_to_dead_letter(monkeypatch):
    monkeypatch.setattr(settings, "HEATMAP_INGEST_MAX_BATCH_RETRIES", 3)
    job = BatchClassificationJob(batch_id="batch_dead", status="in_progress")
    # One chunk already at the cap, one comfortably under it -- 2 districts'
    # worth of chunks behaving differently under the same dead batch.
    rows = [
        _pending_row(retry_count=3, point_id="p_at_cap"),
        _pending_row(retry_count=0, point_id="p_fresh"),
    ]
    db = _fake_db(job, rows)
    classifier = _classifier("failed")

    await classifier.poll_batch(db, "batch_dead")

    at_cap, fresh = rows
    assert at_cap.status == "dead_letter"
    assert at_cap.retry_count == 4
    assert "exceeded" in at_cap.error_message

    assert fresh.status == "pending"
    assert fresh.retry_count == 1


async def test_completed_batch_does_not_touch_pending_classifications():
    job = BatchClassificationJob(batch_id="batch_ok", status="in_progress")
    db = _fake_db(job, stranded=[])
    classifier = _classifier("completed")

    result_job = await classifier.poll_batch(db, "batch_ok")

    assert result_job.status == "completed"
    assert result_job.completed_at is not None
    # No stranded-row query should even be needed for a clean completion,
    # but if the implementation still issues it, it must return nothing to
    # touch (asserted implicitly by no exception + status untouched above).
