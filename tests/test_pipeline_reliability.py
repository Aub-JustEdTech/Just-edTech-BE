"""Unit tests for the pipeline-reliability architectural changes.

Covers the six items from the pipeline-reliability plan:
  Item 5 — add_chunks_returning_ids raises on bad input / Qdrant failure
           instead of silently returning [].
  Item 6 — pre-summarization year gate skips out-of-range school_scraper
           docs before the LLM call.
  Item 4 — delete-before-recreate: step5 + summarizer wipe prior Qdrant
           state before any new write.
  Item 3 — reconcile_stuck_documents finds PROCESSING/PENDING orphans
           past the staleness threshold and re-enqueues them.
  Item 1 — large fields (extracted_text, embeddings, _pdf_pages_text)
           are dropped from the chain payload after the last consuming stage.
  Item 2 — Celery broker Redis split from app-cache Redis (URL fallback
           + override; pipeline tracker on db 3, not db 2).

These tests run without Redis, Postgres, Qdrant, or OpenAI by mocking at
the boundaries (AsyncSessionLocal, VectorStoreFactory, DocumentSummarizer,
process_document_pipeline.delay, celery_app.send_task).

Run:
    poetry run pytest tests/test_pipeline_reliability.py -v
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from unittest.mock import (
    AsyncMock,
    MagicMock,
    patch,
)

import pytest

from app.core.config import settings
from app.models.documents import Document, ProcessingStatus
from app.models.processing_jobs import DocumentProcessingJob, JobStatus
from app.services.vector_store.qdrant_store import QdrantStore
from app.tasks.document_pipeline import PipelineContext


# ===========================================================================
# Item 5 — add_chunks_returning_ids raises instead of returning []
# ===========================================================================


@pytest.fixture
def qdrant_store_with_fake_client() -> QdrantStore:
    """Build a QdrantStore whose self.client is a MagicMock (no network)."""
    with patch.object(QdrantStore, "__init__", lambda self, url=None: None):
        store = QdrantStore()
    store.client = MagicMock()
    # _get_or_create_collection is async; stub to a no-op returning a name.
    store._get_or_create_collection = AsyncMock(return_value="justedtech_1")
    store._upsert_points_individually = MagicMock()
    return store


def test_add_chunks_raises_on_empty_input(qdrant_store_with_fake_client):
    """Empty chunks/embeddings/metadatas must raise, not return []."""
    with pytest.raises(ValueError, match="empty input"):
        asyncio.run(
            qdrant_store_with_fake_client.add_chunks_returning_ids(
                document_id="doc-uuid-1",
                chunks=[],
                embeddings=[],
                metadatas=[],
            )
        )


def test_add_chunks_raises_on_length_mismatch(qdrant_store_with_fake_client):
    """Caller bugs (length mismatch) must raise, not return []."""
    with pytest.raises(ValueError, match="length mismatch"):
        asyncio.run(
            qdrant_store_with_fake_client.add_chunks_returning_ids(
                document_id="doc-uuid-1",
                chunks=["a", "b"],
                embeddings=[[0.1]],
                metadatas=[{"tenant_id": 1}],
            )
        )


def test_add_chunks_raises_on_missing_tenant_id(qdrant_store_with_fake_client):
    """Metadata without tenant_id must raise, not return []."""
    with pytest.raises(ValueError, match="tenant_id missing"):
        asyncio.run(
            qdrant_store_with_fake_client.add_chunks_returning_ids(
                document_id="doc-uuid-1",
                chunks=["a"],
                embeddings=[[0.1]],
                metadatas=[{}],
            )
        )


def test_add_chunks_propagates_qdrant_upsert_failure(qdrant_store_with_fake_client):
    """A real Qdrant upsert failure must propagate, not be swallowed as [].

    Previously, this scenario returned [] silently, leaving the document
    marked COMPLETED with chunk_count out of sync with Qdrant — the
    "MISSING" category in tenant_qdrant_chunk_audit.
    """
    from qdrant_client.http.exceptions import UnexpectedResponse

    # Make the upsert raise something other than the version-incompat branch.
    qdrant_store_with_fake_client.client.upsert = MagicMock(
        side_effect=UnexpectedResponse(
            status_code=500, reason_phrase="boom", content=b"", headers={}
        )
    )
    with pytest.raises(UnexpectedResponse):
        asyncio.run(
            qdrant_store_with_fake_client.add_chunks_returning_ids(
                document_id="doc-uuid-1",
                chunks=["a", "b"],
                embeddings=[[0.1], [0.2]],
                metadatas=[{"tenant_id": 1}, {"tenant_id": 1}],
            )
        )


def test_add_chunks_returns_point_ids_on_success(qdrant_store_with_fake_client):
    """Happy path still returns a list of point IDs (one per chunk)."""
    qdrant_store_with_fake_client.client.upsert = MagicMock()
    point_ids = asyncio.run(
        qdrant_store_with_fake_client.add_chunks_returning_ids(
            document_id="doc-uuid-1",
            chunks=["a", "b"],
            embeddings=[[0.1], [0.2]],
            metadatas=[{"tenant_id": 1}, {"tenant_id": 1}],
        )
    )
    assert len(point_ids) == 2
    assert all(isinstance(pid, str) for pid in point_ids)


# ===========================================================================
# Item 6 — pre-summarization year gate
# ===========================================================================


def _make_ctx_for_year_gate(
    *, source_type: str = "school_scraper"
) -> PipelineContext:
    ctx = PipelineContext(document_id=1, job_id=10, batch_id=None)
    ctx.tenant_id = 1
    ctx.doc_uuid = "doc-uuid-1"
    ctx.source_type = source_type
    ctx.extracted_text = "some text"
    ctx.stage_ids = {}
    return ctx


def _fake_document_with_meeting_date(meeting_date):
    """Build a fake Document row with the given meeting_date."""
    doc = MagicMock(spec=Document)
    doc.id = 1
    doc.name = "minutes.pdf"
    doc.meeting_date = meeting_date
    return doc


def test_step2_5_skips_when_meeting_date_out_of_range():
    """Pre-LLM year gate must short-circuit before summarization for
    out-of-year school_scraper docs. No summary Qdrant write should happen,
    ctx.skip_remaining should be set, and the LLM summarizer should never
    be constructed.
    """
    from app.tasks.document_pipeline import _step2_5_summarize_async

    ctx = _make_ctx_for_year_gate()
    fake_doc = _fake_document_with_meeting_date(
        datetime(2021, 5, 1, tzinfo=UTC).date()
    )

    # Fake DB session: returns our doc.
    fake_db = AsyncMock()
    fake_db.get = AsyncMock(return_value=fake_doc)

    # _create_stage_record / _update_stage_status / _update_job_status all
    # commit/refresh — make them no-ops.
    stage_record = MagicMock()
    stage_record.id = 999

    fake_redis = MagicMock()

    with (
        patch(
            "app.tasks.document_pipeline.AsyncSessionLocal",
            return_value=_fake_async_ctx_mgr(fake_db),
        ),
        patch(
            "app.tasks.document_pipeline._create_stage_record",
            AsyncMock(return_value=stage_record),
        ),
        patch(
            "app.tasks.document_pipeline._update_stage_status",
            AsyncMock(),
        ),
        patch(
            "app.tasks.document_pipeline._update_job_status",
            AsyncMock(),
        ),
        patch(
            "app.services.web_scraper.year_filter.settings.SCHOOL_SCRAPER_ALLOWED_YEARS",
            [2023, 2024, 2025, 2026],
        ),
        # The summarizer must NOT be imported/called.
        patch(
            "app.services.document_processing.summarizer.DocumentSummarizer"
        ) as mock_sum_cls,
    ):
        asyncio.run(_step2_5_summarize_async(ctx, fake_redis))

    assert ctx.skip_remaining is True
    assert ctx.skip_reason is not None
    assert "2021" in ctx.skip_reason
    assert "pre-summarization gate" in ctx.skip_reason
    # Crucially: the LLM summarizer was never instantiated.
    mock_sum_cls.assert_not_called()


def test_step2_5_proceeds_when_meeting_date_in_range():
    """In-range school_scraper doc flows through to the summarizer normally."""
    from app.tasks.document_pipeline import _step2_5_summarize_async

    ctx = _make_ctx_for_year_gate()
    fake_doc = _fake_document_with_meeting_date(
        datetime(2025, 5, 1, tzinfo=UTC).date()
    )

    fake_db = AsyncMock()
    fake_db.get = AsyncMock(return_value=fake_doc)

    stage_record = MagicMock()
    stage_record.id = 999
    fake_redis = MagicMock()
    fake_summarizer = AsyncMock()

    with (
        patch(
            "app.tasks.document_pipeline.AsyncSessionLocal",
            return_value=_fake_async_ctx_mgr(fake_db),
        ),
        patch(
            "app.tasks.document_pipeline._create_stage_record",
            AsyncMock(return_value=stage_record),
        ),
        patch(
            "app.tasks.document_pipeline._update_stage_status",
            AsyncMock(),
        ),
        patch(
            "app.services.web_scraper.year_filter.settings.SCHOOL_SCRAPER_ALLOWED_YEARS",
            [2023, 2024, 2025, 2026],
        ),
        patch(
            "app.services.document_processing.summarizer.DocumentSummarizer",
            return_value=fake_summarizer,
        ),
    ):
        asyncio.run(_step2_5_summarize_async(ctx, fake_redis))

    assert ctx.skip_remaining is False
    # The summarizer.summarize coroutine was awaited once.
    fake_summarizer.summarize.assert_awaited_once()


def test_step2_5_no_year_gate_for_non_school_scraper():
    """Non-scraper docs (e.g. normal uploads) bypass the year gate entirely,
    even if meeting_date is set or None — the gate only applies to
    source_type == 'school_scraper'.
    """
    from app.tasks.document_pipeline import _step2_5_summarize_async

    ctx = _make_ctx_for_year_gate(source_type="upload")
    # Even an out-of-range date should not trigger the gate.
    fake_doc = _fake_document_with_meeting_date(
        datetime(2021, 5, 1, tzinfo=UTC).date()
    )
    fake_db = AsyncMock()
    fake_db.get = AsyncMock(return_value=fake_doc)
    stage_record = MagicMock()
    stage_record.id = 999
    fake_redis = MagicMock()
    fake_summarizer = AsyncMock()

    with (
        patch(
            "app.tasks.document_pipeline.AsyncSessionLocal",
            return_value=_fake_async_ctx_mgr(fake_db),
        ),
        patch(
            "app.tasks.document_pipeline._create_stage_record",
            AsyncMock(return_value=stage_record),
        ),
        patch(
            "app.tasks.document_pipeline._update_stage_status",
            AsyncMock(),
        ),
        patch(
            "app.services.web_scraper.year_filter.settings.SCHOOL_SCRAPER_ALLOWED_YEARS",
            [2023, 2024, 2025, 2026],
        ),
        patch(
            "app.services.document_processing.summarizer.DocumentSummarizer",
            return_value=fake_summarizer,
        ),
    ):
        asyncio.run(_step2_5_summarize_async(ctx, fake_redis))

    assert ctx.skip_remaining is False
    fake_summarizer.summarize.assert_awaited_once()


def test_step2_5_no_year_gate_when_meeting_date_none():
    """A school_scraper doc with meeting_date=None must not be gated at
    stage 2.5 — the post-classification gate in 2.6 is the authoritative
    fallback for the unknown-year case.
    """
    from app.tasks.document_pipeline import _step2_5_summarize_async

    ctx = _make_ctx_for_year_gate()
    fake_doc = _fake_document_with_meeting_date(None)
    fake_db = AsyncMock()
    fake_db.get = AsyncMock(return_value=fake_doc)
    stage_record = MagicMock()
    stage_record.id = 999
    fake_redis = MagicMock()
    fake_summarizer = AsyncMock()

    with (
        patch(
            "app.tasks.document_pipeline.AsyncSessionLocal",
            return_value=_fake_async_ctx_mgr(fake_db),
        ),
        patch(
            "app.tasks.document_pipeline._create_stage_record",
            AsyncMock(return_value=stage_record),
        ),
        patch(
            "app.tasks.document_pipeline._update_stage_status",
            AsyncMock(),
        ),
        patch(
            "app.services.web_scraper.year_filter.settings.SCHOOL_SCRAPER_ALLOWED_YEARS",
            [2023, 2024, 2025, 2026],
        ),
        patch(
            "app.services.document_processing.summarizer.DocumentSummarizer",
            return_value=fake_summarizer,
        ),
    ):
        asyncio.run(_step2_5_summarize_async(ctx, fake_redis))

    assert ctx.skip_remaining is False
    fake_summarizer.summarize.assert_awaited_once()


# ===========================================================================
# Item 4 — delete-before-recreate
# ===========================================================================


def test_step5_store_calls_delete_before_upsert():
    """step5 must call delete_document + delete_document_summary BEFORE
    adding new chunks/summaries, regardless of how the doc arrived.
    """
    from app.tasks.document_pipeline import _step5_store_async

    ctx = PipelineContext(document_id=1, job_id=10, batch_id=None)
    ctx.tenant_id = 1
    ctx.doc_uuid = "doc-uuid-1"
    ctx.chunks = ["a", "b"]
    ctx.embeddings = [[0.1], [0.2]]
    ctx.chunk_metadatas = [{}, {}]
    ctx.doc_metadata = {}
    ctx.source_type = "upload"
    ctx.stage_ids = {}

    fake_db = AsyncMock()
    fake_doc = MagicMock(spec=Document)
    fake_doc.name = "test.txt"
    fake_doc.source_metadata = {}
    fake_doc.source_type = "upload"
    fake_doc.meeting_doc_type = None
    fake_doc.meeting_body = None
    fake_doc.document_quality = "clean_digital"
    fake_doc.state = None
    fake_doc.district_name = None
    fake_doc.school_year = None
    fake_doc.quarter_month = None
    fake_doc.meeting_date = None
    fake_db.get = AsyncMock(return_value=fake_doc)

    stage_record = MagicMock()
    stage_record.id = 1

    fake_vs = AsyncMock()
    # delete returns truthy so we can count it.
    fake_vs.delete_document = AsyncMock(return_value=True)
    fake_vs.delete_document_summary = AsyncMock(return_value=True)
    fake_vs.add_chunks_returning_ids = AsyncMock(return_value=["pid-1", "pid-2"])

    fake_redis = MagicMock()

    call_order: list[str] = []

    async def _record_delete(*a, **kw):
        call_order.append("delete_document")
        return True

    async def _record_delete_summary(*a, **kw):
        call_order.append("delete_document_summary")
        return True

    async def _record_add(*a, **kw):
        call_order.append("add_chunks_returning_ids")
        return ["pid-1", "pid-2"]

    fake_vs.delete_document = _record_delete
    fake_vs.delete_document_summary = _record_delete_summary
    fake_vs.add_chunks_returning_ids = _record_add

    with (
        patch(
            "app.tasks.document_pipeline.AsyncSessionLocal",
            return_value=_fake_async_ctx_mgr(fake_db),
        ),
        patch(
            "app.tasks.document_pipeline._create_stage_record",
            AsyncMock(return_value=stage_record),
        ),
        patch(
            "app.tasks.document_pipeline._update_stage_status",
            AsyncMock(),
        ),
        patch(
            "app.tasks.document_pipeline.VectorStoreFactory.create",
            return_value=fake_vs,
        ),
        patch("app.tasks.document_pipeline._process_images", AsyncMock()),
    ):
        asyncio.run(_step5_store_async(ctx, fake_redis))

    # Both deletes happened before the upsert.
    assert call_order == [
        "delete_document",
        "delete_document_summary",
        "add_chunks_returning_ids",
    ], f"Expected delete-before-recreate order, got {call_order}"


def test_step5_store_continues_if_delete_fails():
    """A delete failure must not abort the store — the subsequent upsert
    will surface real Qdrant connectivity issues. (Fresh documents may
    have no prior points to delete.)
    """
    from app.tasks.document_pipeline import _step5_store_async

    ctx = PipelineContext(document_id=1, job_id=10, batch_id=None)
    ctx.tenant_id = 1
    ctx.doc_uuid = "doc-uuid-1"
    ctx.chunks = ["a"]
    ctx.embeddings = [[0.1]]
    ctx.chunk_metadatas = [{}]
    ctx.doc_metadata = {}
    ctx.source_type = "upload"
    ctx.stage_ids = {}

    fake_db = AsyncMock()
    fake_doc = MagicMock(spec=Document)
    fake_doc.name = "test.txt"
    fake_doc.source_metadata = {}
    fake_doc.source_type = "upload"
    fake_doc.meeting_doc_type = None
    fake_doc.meeting_body = None
    fake_doc.document_quality = "clean_digital"
    fake_doc.state = None
    fake_doc.district_name = None
    fake_doc.school_year = None
    fake_doc.quarter_month = None
    fake_doc.meeting_date = None
    fake_db.get = AsyncMock(return_value=fake_doc)

    stage_record = MagicMock()
    stage_record.id = 1

    fake_vs = AsyncMock()
    fake_vs.delete_document = AsyncMock(side_effect=RuntimeError("boom"))
    fake_vs.delete_document_summary = AsyncMock(side_effect=RuntimeError("boom"))
    fake_vs.add_chunks_returning_ids = AsyncMock(return_value=["pid-1"])

    fake_redis = MagicMock()

    with (
        patch(
            "app.tasks.document_pipeline.AsyncSessionLocal",
            return_value=_fake_async_ctx_mgr(fake_db),
        ),
        patch(
            "app.tasks.document_pipeline._create_stage_record",
            AsyncMock(return_value=stage_record),
        ),
        patch(
            "app.tasks.document_pipeline._update_stage_status",
            AsyncMock(),
        ),
        patch(
            "app.tasks.document_pipeline.VectorStoreFactory.create",
            return_value=fake_vs,
        ),
        patch("app.tasks.document_pipeline._process_images", AsyncMock()),
    ):
        # Should NOT raise — delete failures are logged and swallowed.
        asyncio.run(_step5_store_async(ctx, fake_redis))

    # The upsert was still called.
    fake_vs.add_chunks_returning_ids.assert_awaited_once()


def test_summarizer_indexes_with_delete_before_add():
    """summarizer._index_summary must call delete_document_summary before
    add_document_summary so a re-summarized doc doesn't accumulate orphan
    summary points (the tenant-2 inflated-summary-count bug).
    """
    from app.services.document_processing.summarizer import DocumentSummarizer

    summarizer = DocumentSummarizer.__new__(DocumentSummarizer)
    summarizer._embedding_service = MagicMock()
    summarizer._embedding_service.generate_embeddings = AsyncMock(
        return_value=[[0.1, 0.2]]
    )

    call_order: list[str] = []

    async def _rec_delete(*a, **kw):
        call_order.append("delete_document_summary")
        return True

    async def _rec_add(*a, **kw):
        call_order.append("add_document_summary")
        return True

    fake_vs = AsyncMock()
    fake_vs.delete_document_summary = _rec_delete
    fake_vs.add_document_summary = _rec_add
    fake_vs.hasattr = lambda _vs, name: name in (
        "delete_document_summary",
        "add_document_summary",
    )

    parsed = {
        "doc_type": "minutes",
        "date_range": "2025-01",
        "summary": "A meeting happened.",
        "key_topics": [],
        "key_entities": [],
    }

    with (
        patch(
            "app.services.vector_store.factory.VectorStoreFactory.create",
            return_value=fake_vs,
        ),
    ):
        asyncio.run(
            summarizer._index_summary(
                parsed=parsed,
                document_id=42,
                doc_uuid="doc-uuid-42",
                document_name="minutes.pdf",
                tenant_id=1,
            )
        )

    assert call_order == ["delete_document_summary", "add_document_summary"]


# ===========================================================================
# Item 3 — reconcile_stuck_documents
# ===========================================================================


def _fake_stuck_doc(
    *,
    doc_id: int = 1,
    tenant_id: int = 1,
    status: ProcessingStatus = ProcessingStatus.PROCESSING,
    doc_uuid: str = "doc-uuid-1",
    document_type: str = ".pdf",
    name: str = "minutes.pdf",
    hours_stale: int = 2,
) -> Document:
    """Build a fake Document stuck in PROCESSING/PENDING."""
    doc = MagicMock(spec=Document)
    doc.id = doc_id
    doc.tenant_id = tenant_id
    doc.doc_id = doc_uuid
    # Use the real enum — MagicMock(spec=Document) gives us the enum value
    # already; don't try to reassign .value on the enum itself.
    doc.processing_status = status
    doc.document_type = document_type
    doc.name = name
    doc.updated_at = datetime.now(UTC).replace(tzinfo=None) - timedelta(
        hours=hours_stale
    )
    return doc


def test_reconcile_finds_and_requeues_stuck_docs():
    """The reconciliation task finds PROCESSING + PENDING docs past the
    staleness threshold, deletes their partial Qdrant state, resets them
    to PENDING, and re-enqueues process_document_pipeline.

    This is the automated safety net for the broker-message-loss failure
    mode that orphaned 3,000+ documents overnight.
    """
    from app.tasks.stuck_document_reconciliation_tasks import _reconcile_async

    stuck_processing = _fake_stuck_doc(
        doc_id=1, status=ProcessingStatus.PROCESSING
    )
    stuck_pending = _fake_stuck_doc(
        doc_id=2, status=ProcessingStatus.PENDING
    )

    # First AsyncSession call: list tenants.
    # Subsequent calls: per-doc resets.
    db_calls: list[str] = []

    class _FakeSession:
        def __init__(self):
            self._call_idx = 0

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def execute(self, stmt):
            # First call returns tenant_ids.
            if not db_calls:
                db_calls.append("tenants")
                result = MagicMock()
                result.scalars.return_value.all.return_value = [1]
                return result
            # Second call returns the stuck docs list.
            if len(db_calls) == 1:
                db_calls.append("docs")
                result = MagicMock()
                result.scalars.return_value.all.return_value = [
                    stuck_processing,
                    stuck_pending,
                ]
                return result
            # Per-doc reset calls return nothing useful.
            return MagicMock()

        async def get(self, model, pk):
            # Return the right doc for its id.
            if pk == 1:
                d = _fake_stuck_doc(doc_id=1, status=ProcessingStatus.PROCESSING)
                # After reset, status flips to PENDING.
                d.processing_status = ProcessingStatus.PENDING
                return d
            if pk == 2:
                d = _fake_stuck_doc(doc_id=2, status=ProcessingStatus.PENDING)
                d.processing_status = ProcessingStatus.PENDING
                return d
            return None

        def add(self, obj):
            obj.id = 999

        async def flush(self):
            pass

        async def commit(self):
            pass

    fake_vs = AsyncMock()
    fake_vs.delete_document = AsyncMock(return_value=True)
    fake_vs.delete_document_summary = AsyncMock(return_value=True)

    enqueued: list[tuple[int, int]] = []

    def _fake_delay(doc_id, job_id):
        enqueued.append((doc_id, job_id))

    with (
        patch(
            "app.tasks.stuck_document_reconciliation_tasks.AsyncSessionLocal",
            side_effect=lambda: _FakeSession(),
        ),
        patch(
            "app.tasks.stuck_document_reconciliation_tasks.VectorStoreFactory.create",
            return_value=fake_vs,
        ),
        patch(
            "app.tasks.stuck_document_reconciliation_tasks.settings.VECTOR_STORE_TYPE",
            "qdrant",
        ),
        patch(
            "app.tasks.document_pipeline.process_document_pipeline",
        ) as mock_pdp,
    ):
        mock_pdp.delay = _fake_delay
        result = asyncio.run(_reconcile_async(stale_minutes=45))

    assert result["tenants_scanned"] == 1
    assert result["candidates"] == 2
    assert result["reset"] == 2
    assert result["chunks_deleted"] == 2
    assert result["summaries_deleted"] == 2
    # Both docs were re-enqueued with their new job IDs.
    assert len(enqueued) == 2
    assert {doc_id for doc_id, _ in enqueued} == {1, 2}


def test_reconcile_no_op_when_no_stuck_docs():
    """When there are no stuck documents, the task returns zeros and does
    NOT attempt any re-enqueue (avoids pointless churn).
    """
    from app.tasks.stuck_document_reconciliation_tasks import _reconcile_async

    class _FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def execute(self, stmt):
            # Returns an empty tenant list.
            result = MagicMock()
            result.scalars.return_value.all.return_value = []
            return result

    with (
        patch(
            "app.tasks.stuck_document_reconciliation_tasks.AsyncSessionLocal",
            side_effect=lambda: _FakeSession(),
        ),
        patch(
            "app.tasks.stuck_document_reconciliation_tasks.VectorStoreFactory.create",
        ),
        patch(
            "app.tasks.document_pipeline.process_document_pipeline",
        ) as mock_pdp,
    ):
        result = asyncio.run(_reconcile_async(stale_minutes=45))

    assert result["candidates"] == 0
    assert result["reset"] == 0
    mock_pdp.delay.assert_not_called()


# ===========================================================================
# Item 1 — drop large fields from the chain payload
# ===========================================================================


def test_step3_drops_extracted_text_in_wrapper_after_chunking():
    """After step 3 succeeds, the sync wrapper clears extracted_text from
    the returned context — it bloats every subsequent Redis serialization
    and is not needed by stages 4/5/6. This test isolates the wrapper's
    drop behavior by mocking the async impl.
    """
    from app.tasks.document_pipeline import step3_chunk_text

    ctx = PipelineContext(document_id=1, job_id=10, batch_id=None)
    ctx.tenant_id = 1
    ctx.doc_uuid = "doc-uuid-1"
    ctx.document_type = ".pdf"
    ctx.extracted_text = "x" * 1_000_000  # 1MB of text
    ctx.doc_metadata = {}
    ctx.stage_ids = {}
    ctx.source_type = "upload"

    fake_redis = MagicMock()
    fake_redis.update_stage = MagicMock()
    fake_redis.set_document_status = MagicMock()

    with (
        patch(
            "app.tasks.document_pipeline._step3_chunk_async",
            AsyncMock(),
        ),
        patch(
            "app.tasks.document_pipeline.get_redis_tracker",
            return_value=fake_redis,
        ),
        patch(
            "app.tasks.document_pipeline.get_event_loop"
        ) as mock_loop,
    ):
        mock_loop.run_until_complete = lambda coro: asyncio.run(coro) if coro else None
        result = step3_chunk_text(ctx.to_dict())

    assert result["extracted_text"] == ""


def test_step3_pops_pdf_pages_text_inside_async_impl():
    """The async impl pops _pdf_pages_text from doc_metadata after chunking
    so the full per-page text doesn't flow through stages 4/5/6 (where it
    would duplicate the already-chunked text in the chain payload).
    """
    from app.tasks.document_pipeline import _step3_chunk_async

    ctx = PipelineContext(document_id=1, job_id=10, batch_id=None)
    ctx.tenant_id = 1
    ctx.doc_uuid = "doc-uuid-1"
    ctx.document_type = ".pdf"
    ctx.extracted_text = ""
    ctx.doc_metadata = {
        "_pdf_pages_text": ["page 1 text", "page 2 text"],
    }
    ctx.chunks = []
    ctx.chunk_metadatas = []
    ctx.stage_ids = {}
    ctx.source_type = "upload"

    fake_db = AsyncMock()
    stage_record = MagicMock()
    stage_record.id = 1
    fake_chatbot_config = MagicMock()
    fake_chatbot_config.id = 1
    fake_chunking_config = {"chunk_size": 1000, "chunk_overlap": 100}
    fake_redis = MagicMock()

    with (
        patch(
            "app.tasks.document_pipeline.AsyncSessionLocal",
            return_value=_fake_async_ctx_mgr(fake_db),
        ),
        patch(
            "app.tasks.document_pipeline._create_stage_record",
            AsyncMock(return_value=stage_record),
        ),
        patch(
            "app.tasks.document_pipeline._update_stage_status",
            AsyncMock(),
        ),
        patch(
            "app.tasks.document_pipeline.chatbot_config_service.get_default_chatbot_config",
            AsyncMock(return_value=fake_chatbot_config),
        ),
        patch(
            "app.tasks.document_pipeline.chatbot_config_service.get_chunking_config",
            AsyncMock(return_value=fake_chunking_config),
        ),
    ):
        asyncio.run(_step3_chunk_async(ctx, fake_redis))

    assert "_pdf_pages_text" not in ctx.doc_metadata


def test_step5_drops_embeddings_after_storing():
    """After step 5 succeeds, embeddings (the largest per-doc field,
    1536-3072 floats × N chunks) must be cleared from the chain payload
    since stage 6 doesn't use them.
    """
    from app.tasks.document_pipeline import step5_store_vectors

    ctx = PipelineContext(document_id=1, job_id=10, batch_id=None)
    ctx.tenant_id = 1
    ctx.doc_uuid = "doc-uuid-1"
    ctx.chunks = ["a", "b"]
    ctx.embeddings = [[0.1] * 1536, [0.2] * 1536]
    ctx.chunk_metadatas = [{}, {}]
    ctx.doc_metadata = {}
    ctx.source_type = "upload"
    ctx.stage_ids = {}
    ctx.temp_file_path = None  # nothing to clean up

    fake_redis = MagicMock()
    fake_redis.update_stage = MagicMock()
    fake_redis.set_document_status = MagicMock()
    fake_redis.remove_active_job = MagicMock()

    fake_task = MagicMock()
    fake_task.request.retries = 0
    step5_store_vectors.__self__ = fake_task

    with (
        patch(
            "app.tasks.document_pipeline._step5_store_async",
            AsyncMock(),
        ),
        patch(
            "app.tasks.document_pipeline.get_redis_tracker",
            return_value=fake_redis,
        ),
        patch(
            "app.tasks.document_pipeline.get_event_loop"
        ) as mock_loop,
    ):
        mock_loop.run_until_complete = lambda coro: asyncio.run(coro) if coro else None
        result = step5_store_vectors(ctx.to_dict())

    assert result["embeddings"] == []
    # chunks preserved — stage 6 still needs them for chunk_text.
    assert result["chunks"] == ["a", "b"]


# ===========================================================================
# Item 2 — broker Redis split
# ===========================================================================


def test_celery_broker_url_falls_back_to_app_redis_when_unset(monkeypatch):
    """Without CELERY_BROKER_REDIS_* env vars (local dev), the broker URL
    must fall back to the app-cache REDIS_HOST:REDIS_PORT db 2 — preserving
    the old single-Redis behavior.
    """
    from app.core.config import Settings

    s = Settings(
        REDIS_HOST="localhost",
        REDIS_PORT=6379,
        REDIS_DB=0,
        BACKEND_CORS_ORIGINS=["http://localhost:3000"],
        SECRET_KEY="test",  # noqa: S106
    )
    assert s.CELERY_BROKER_URL == "redis://localhost:6379/2"
    assert s.CELERY_BACKEND_URL == "redis://localhost:6379/2"


def test_celery_broker_url_uses_dedicated_host_when_set(monkeypatch):
    """When CELERY_BROKER_REDIS_HOST is set (prod), the broker URL points
    at the dedicated redis-broker container, not the app-cache Redis.
    """
    from app.core.config import Settings

    s = Settings(
        REDIS_HOST="redis",
        REDIS_PORT=6379,
        REDIS_DB=0,
        CELERY_BROKER_REDIS_HOST="redis-broker",
        CELERY_BROKER_REDIS_PORT=6379,
        BACKEND_CORS_ORIGINS=["http://localhost:3000"],
        SECRET_KEY="test",  # noqa: S106
    )
    assert s.CELERY_BROKER_URL == "redis://redis-broker:6379/2"
    # App cache still points at `redis`.
    assert s.REDIS_URL == "redis://redis:6379/0"


def test_celery_backend_url_inherits_broker_defaults():
    """CELERY_BACKEND_* fields fall back through: explicit backend →
    explicit broker → app-cache REDIS_*.
    """
    from app.core.config import Settings

    s = Settings(
        REDIS_HOST="redis",
        REDIS_PORT=6379,
        REDIS_DB=0,
        CELERY_BROKER_REDIS_HOST="redis-broker",
        CELERY_BROKER_REDIS_PORT=6379,
        BACKEND_CORS_ORIGINS=["http://localhost:3000"],
        SECRET_KEY="test",  # noqa: S106
    )
    # Backend defaults to broker's host/port.
    assert s.CELERY_BACKEND_URL == "redis://redis-broker:6379/2"


def test_pipeline_tracker_uses_db_3_not_broker_db_2():
    """The RedisPipelineTracker must live on db 3 of the app-cache Redis,
    NOT db 2 (the broker DB). Co-locating status hashes with in-flight
    Celery messages meant a broker OOM also dropped stage progress, and
    a noeviction broker rejected status writes under load.
    """
    import inspect

    from app.utils.redis_pipeline import RedisPipelineTracker

    src = inspect.getsource(RedisPipelineTracker.__init__)
    assert "db=3" in src, (
        "RedisPipelineTracker must use db=3 (app-cache), not db=2 (broker)"
    )


def test_celery_app_uses_settings_urls():
    """celery_app.Celery() must be constructed with settings.CELERY_BROKER_URL
    and settings.CELERY_BACKEND_URL, NOT a hardcoded redis://.../2 string.
    """
    import inspect

    from app.celery_app import celery_app

    # The Celery app's broker/backend come from settings.* URLs, which in
    # turn honor CELERY_BROKER_REDIS_* overrides. Hardcoding would break
    # the split.
    assert celery_app.conf.broker_url == settings.CELERY_BROKER_URL
    assert celery_app.conf.result_backend == settings.CELERY_BACKEND_URL

    # Sanity: the construction source references the settings properties,
    # not a f-string with hardcoded REDIS_HOST.
    import app.celery_app as celery_mod

    src = inspect.getsource(celery_mod)
    assert "settings.CELERY_BROKER_URL" in src
    assert "settings.CELERY_BACKEND_URL" in src
    # And critically, no hardcoded /2 f-string remains.
    assert "redis://{settings.REDIS_HOST}" not in src


# ===========================================================================
# Helpers
# ===========================================================================


class _fake_async_ctx_mgr:
    """Mimic `async with AsyncSessionLocal() as db:` for tests.

    AsyncSessionLocal() returns an async context manager; this wraps a
    plain mock so `async with` works.
    """

    def __init__(self, session_mock):
        self._session = session_mock

    async def __aenter__(self):
        return self._session

    async def __aexit__(self, *args):
        return False
