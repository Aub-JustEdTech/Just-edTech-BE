"""Unit tests for apply_batch_results()'s incremental-commit hardening.

Regression coverage for the incident on 2026-08-12: an 8,099-chunk batch
completed on OpenAI's side, but a NotNullViolationError from a broken
heatmap_aggregate.id column poisoned the DB session, and because the
original code committed once at the very end, ALL 8,099 chunks' progress
rolled back with it. These tests use a small 3-chunk fixture -- enough to
prove per-row processing survives a downstream aggregate failure -- rather
than reproducing the incident at full scale.

Everything is mocked: no real DB, no real Qdrant, no real OpenAI call.

Run:
    poetry run pytest tests/test_batch_classifier_apply_results.py -v
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core.config import settings
from app.models.pending_classification import PendingClassification
from app.services.heatmap_ingest.batch_classifier import BatchClassifier

pytestmark = pytest.mark.asyncio


def _pending_row(id_: int, point_id: str):
    return PendingClassification(
        id=id_,
        document_id=1,
        qdrant_point_id=point_id,
        chunk_index=0,
        chunk_text="the school committee discussed the budget",
        status="submitted",
        batch_id="batch_test",
    )


def _classification_line(custom_id: str, *, off_topic: bool = False):
    # "sex_education" is one of the real allowed TOPICS values; topic_tags
    # left empty to avoid also needing a valid TopicTag(category, subtopic)
    # pair -- irrelevant to what these tests are checking.
    body = {
        "topics": [] if off_topic else ["sex_education"],
        "action_types": [],
        "subtopics": [],
        "evidence_quote": "",
        "off_topic": off_topic,
        "topic_tags": [],
        "action_stage": None,
        "speakers": [],
    }
    return json.dumps(
        {
            "custom_id": custom_id,
            "response": {
                "body": {
                    "choices": [{"message": {"content": json.dumps(body)}}]
                }
            },
            "error": None,
        }
    )


def _classifier(rows, monkeypatch, *, aggregate_side_effect=None):
    """A BatchClassifier with every I/O boundary faked: OpenAI client, S3,
    the vector store, and the document/tenant lookups."""
    instance = BatchClassifier.__new__(BatchClassifier)

    output_text = "\n".join(_classification_line(str(r.id)) for r in rows)
    instance._client = SimpleNamespace(
        batches=SimpleNamespace(
            retrieve=AsyncMock(
                return_value=SimpleNamespace(output_file_id="file-1")
            )
        ),
        files=SimpleNamespace(
            content=AsyncMock(
                return_value=SimpleNamespace(
                    aread=AsyncMock(return_value=output_text.encode("utf-8"))
                )
            )
        ),
    )
    instance._s3 = SimpleNamespace(upload_file_object=AsyncMock())

    job = SimpleNamespace(
        batch_id="batch_test", status="completed", output_jsonl_s3_key=None
    )
    monkeypatch.setattr(instance, "poll_batch", AsyncMock(return_value=job))
    monkeypatch.setattr(
        instance, "_tenant_id_for_doc", AsyncMock(return_value=2)
    )
    monkeypatch.setattr(
        instance,
        "_upsert_heatmap_aggregate",
        AsyncMock(side_effect=aggregate_side_effect),
    )

    fake_vector_store = SimpleNamespace(update_metadata=AsyncMock())
    monkeypatch.setattr(
        "app.services.vector_store.factory.VectorStoreFactory.create",
        MagicMock(return_value=fake_vector_store),
    )

    return instance, job, fake_vector_store


def _fake_db(rows):
    result = MagicMock()
    result.scalars.return_value.all.return_value = rows
    db = MagicMock()
    db.execute = AsyncMock(return_value=result)
    db.commit = AsyncMock()
    db.rollback = AsyncMock()
    return db


def _fresh_rows():
    """A new set of 3 mutable ORM objects per test -- these get their
    `.status` mutated in place by apply_batch_results, so sharing one list
    across tests would leak state between them."""
    return [_pending_row(101, "p1"), _pending_row(102, "p2"), _pending_row(103, "p3")]


async def test_aggregate_failure_does_not_roll_back_applied_chunks(monkeypatch):
    """The exact incident: heatmap_aggregate upsert raises -- the 3 chunks
    that already succeeded must stay 'applied', not get wiped out."""
    rows = _fresh_rows()
    classifier, job, _ = _classifier(
        rows, monkeypatch, aggregate_side_effect=RuntimeError("NotNullViolationError")
    )
    db = _fake_db(rows)

    stats = await classifier.apply_batch_results(db, "batch_test")

    assert stats["applied"] == 3
    assert stats["aggregate_failed"] is True
    for row in rows:
        assert row.status == "applied"
        assert row.error_message is None

    # Per-chunk progress was flushed (committed) before the aggregate step,
    # and rolled back only once for the failed aggregate attempt -- not for
    # the chunk results themselves.
    assert db.commit.await_count >= 1
    db.rollback.assert_awaited_once()
    assert job.status == "applied"


async def test_aggregate_success_still_commits_and_flips_job_applied(monkeypatch):
    rows = _fresh_rows()
    classifier, job, _ = _classifier(rows, monkeypatch, aggregate_side_effect=None)
    db = _fake_db(rows)

    stats = await classifier.apply_batch_results(db, "batch_test")

    assert stats["applied"] == 3
    assert "aggregate_failed" not in stats
    db.rollback.assert_not_awaited()
    assert job.status == "applied"


async def test_periodic_commit_fires_before_the_batch_finishes(monkeypatch):
    """With a commit threshold smaller than the row count, a commit must
    happen mid-loop -- not just once at the very end."""
    monkeypatch.setattr(settings, "HEATMAP_INGEST_APPLY_COMMIT_BATCH_SIZE", 2)
    rows = _fresh_rows()
    classifier, _, _ = _classifier(rows, monkeypatch, aggregate_side_effect=None)
    db = _fake_db(rows)

    await classifier.apply_batch_results(db, "batch_test")

    # mid-loop checkpoint (after row 2) + end-of-loop flush + aggregate
    # commit + final job-status commit.
    assert db.commit.await_count == 4


async def test_qdrant_failure_marks_only_that_chunk_failed(monkeypatch):
    # Retries disabled so each row consumes exactly one side_effect entry --
    # the retry behavior itself is covered by TestUpdateMetadataWithRetry.
    monkeypatch.setattr(settings, "HEATMAP_INGEST_APPLY_SET_PAYLOAD_RETRIES", 0)
    rows = _fresh_rows()
    classifier, _, fake_vector_store = _classifier(
        rows, monkeypatch, aggregate_side_effect=None
    )
    fake_vector_store.update_metadata.side_effect = [
        None,
        RuntimeError("timed out"),
        None,
    ]
    db = _fake_db(rows)

    stats = await classifier.apply_batch_results(db, "batch_test")

    assert stats["applied"] == 2
    assert stats["failed"] == 1
    assert rows[0].status == "applied"
    assert rows[1].status == "failed"
    assert "qdrant set_payload" in rows[1].error_message
    assert rows[2].status == "applied"


class TestUpdateMetadataWithRetry:
    @pytest.fixture(autouse=True)
    def no_real_sleep(self, monkeypatch):
        monkeypatch.setattr(
            "app.services.heatmap_ingest.batch_classifier.asyncio.sleep",
            AsyncMock(),
        )

    async def test_succeeds_after_transient_failures(self, monkeypatch):
        monkeypatch.setattr(
            settings, "HEATMAP_INGEST_APPLY_SET_PAYLOAD_RETRIES", 2
        )
        classifier = BatchClassifier.__new__(BatchClassifier)
        store = SimpleNamespace(
            update_metadata=AsyncMock(
                side_effect=[TimeoutError(), TimeoutError(), None]
            )
        )

        await classifier._update_metadata_with_retry(
            store, chunk_ids=["p1"], metadata={"classified": True}, tenant_id=2
        )

        assert store.update_metadata.await_count == 3

    async def test_gives_up_after_max_retries(self, monkeypatch):
        monkeypatch.setattr(
            settings, "HEATMAP_INGEST_APPLY_SET_PAYLOAD_RETRIES", 2
        )
        classifier = BatchClassifier.__new__(BatchClassifier)
        store = SimpleNamespace(
            update_metadata=AsyncMock(side_effect=TimeoutError("timed out"))
        )

        with pytest.raises(TimeoutError):
            await classifier._update_metadata_with_retry(
                store, chunk_ids=["p1"], metadata={"classified": True}, tenant_id=2
            )

        assert store.update_metadata.await_count == 3
