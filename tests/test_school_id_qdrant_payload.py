"""Regression tests for school_id on Qdrant chunk payloads (issue #29).

Two failure modes previously left school_id=None on every school_scraper
chunk:
  1. step2 assigned processor.extract_metadata() over ctx.doc_metadata,
     wiping the source_metadata copy from step 1.
  2. step5 heatmap_doc_meta denorm omitted school_id even though it was
     still available on Document.source_metadata.

These tests mock DB / processor / vector-store boundaries — no Redis,
Postgres, Qdrant, or OpenAI required.

Run:
    poetry run pytest tests/test_school_id_qdrant_payload.py -v
"""

from __future__ import annotations

import asyncio
from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch

from app.models.documents import Document
from app.tasks.document_pipeline import PipelineContext


class _fake_async_ctx_mgr:
    """Async context manager that yields a pre-built fake session."""

    def __init__(self, session):
        self._session = session

    async def __aenter__(self):
        return self._session

    async def __aexit__(self, *exc):
        return False


def test_step2_preserves_ingest_school_id_when_extracting(tmp_path):
    """Processor file metadata must merge into doc_metadata, not replace it.

    Step 1 copies source_metadata.school_id onto ctx.doc_metadata; step 2
    used to wipe it with PDF author/title keys.
    """
    from app.tasks.document_pipeline import _step2_extract_async

    # Stage completion calls os.path.getsize — give it a real file.
    temp_pdf = tmp_path / "fake.pdf"
    temp_pdf.write_bytes(b"%PDF-1.4 sample")

    ctx = PipelineContext(document_id=1, job_id=10, batch_id=None)
    ctx.tenant_id = 4
    ctx.doc_uuid = "school-02690000-abc"
    ctx.document_type = ".pdf"
    ctx.temp_file_path = str(temp_pdf)
    ctx.stage_ids = {}
    # Simulate step 1 having already copied source_metadata.
    ctx.doc_metadata = {
        "school_id": 399,
        "school_org_code": "02690000",
        "school_name": "Abington",
        "source_media_url": "https://example.com/minutes.pdf",
    }

    fake_db = AsyncMock()
    stage_record = MagicMock()
    stage_record.id = 1
    fake_redis = MagicMock()

    class _FakeProcessor:
        ocr_used = False

        def extract_metadata(self, _path):
            return {
                "page_count": 3,
                "author": "School Committee",
                "title": "Minutes",
                "subject": "",
                "creator": "Acrobat",
                "producer": "Acrobat",
                # Hostile collision: processor must NOT overwrite ingest school_id.
                "school_id": "should-not-win",
            }

        def extract_text_by_page(self, _path):
            return ["page one text", "page two"]

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
            "app.tasks.document_pipeline.ProcessorFactory.get_processor",
            return_value=_FakeProcessor(),
        ),
    ):
        asyncio.run(_step2_extract_async(ctx, fake_redis))

    assert ctx.doc_metadata["school_id"] == 399
    assert ctx.doc_metadata["school_org_code"] == "02690000"
    assert ctx.doc_metadata["school_name"] == "Abington"
    # File metadata still lands.
    assert ctx.doc_metadata["page_count"] == 3
    assert ctx.doc_metadata["author"] == "School Committee"
    assert ctx.doc_metadata["title"] == "Minutes"
    # Internal PDF page payload is present.
    assert "_pdf_pages_text" in ctx.doc_metadata
    assert ctx.extracted_text


def test_step5_writes_school_id_onto_chunk_payload():
    """school_scraper chunks must carry int school_id from source_metadata."""
    from app.tasks.document_pipeline import _step5_store_async

    ctx = PipelineContext(document_id=1, job_id=10, batch_id=None)
    ctx.tenant_id = 4
    ctx.doc_uuid = "school-02690000-abc"
    ctx.document_type = ".pdf"
    ctx.chunks = ["chunk about parental rights", "second chunk"]
    ctx.embeddings = [[0.1], [0.2]]
    ctx.chunk_metadatas = [{"page_number": 1}, {"page_number": 2}]
    # Even if step2 merge failed, heatmap_doc_meta must still recover
    # school_id from Document.source_metadata.
    ctx.doc_metadata = {"page_count": 2, "author": "Someone"}
    ctx.source_type = "school_scraper"
    ctx.stage_ids = {}
    ctx.meeting_doc_type = "Minutes"
    ctx.meeting_body = "School Committee"

    fake_db = AsyncMock()
    fake_doc = MagicMock(spec=Document)
    fake_doc.name = "minutes.pdf"
    fake_doc.source_metadata = {
        "school_id": 399,
        "school_org_code": "02690000",
        "school_name": "Abington",
        "source_media_url": "https://example.com/minutes.pdf",
        "source_page_url": "https://example.com/archive",
    }
    fake_doc.source_type = "school_scraper"
    fake_doc.meeting_doc_type = "Minutes"
    fake_doc.meeting_body = "School Committee"
    fake_doc.document_quality = "clean_digital"
    fake_doc.state = "MA"
    fake_doc.district_name = "Abington"
    fake_doc.school_year = "2024-2025"
    fake_doc.quarter_month = "2024-10"
    fake_doc.meeting_date = date(2024, 10, 15)
    fake_db.get = AsyncMock(return_value=fake_doc)

    stage_record = MagicMock()
    stage_record.id = 1

    captured_metadatas: list[dict] = []

    async def _capture_add(*, document_id, chunks, embeddings, metadatas):
        captured_metadatas.extend(metadatas)
        return ["pid-1", "pid-2"]

    fake_vs = AsyncMock()
    fake_vs.delete_document = AsyncMock(return_value=True)
    fake_vs.delete_document_summary = AsyncMock(return_value=True)
    fake_vs.add_chunks_returning_ids = _capture_add

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
        asyncio.run(_step5_store_async(ctx, fake_redis))

    assert len(captured_metadatas) == 2
    for meta in captured_metadatas:
        assert meta["school_id"] == 399
        assert isinstance(meta["school_id"], int)
        assert meta["school_org_code"] == "02690000"
        assert meta["school_name"] == "Abington"
        assert meta["district_name"] == "Abington"
        assert meta["state"] == "MA"
        assert meta["source_media_url"] == "https://example.com/minutes.pdf"
        assert meta["classified"] is False


def test_step5_coerces_string_school_id_to_int():
    """JSONB may round-trip school_id as a string; coerce so filters match."""
    from app.tasks.document_pipeline import _step5_store_async

    ctx = PipelineContext(document_id=1, job_id=10, batch_id=None)
    ctx.tenant_id = 4
    ctx.doc_uuid = "school-00010000-xyz"
    ctx.document_type = ".pdf"
    ctx.chunks = ["one"]
    ctx.embeddings = [[0.1]]
    ctx.chunk_metadatas = [{}]
    ctx.doc_metadata = {}
    ctx.source_type = "school_scraper"
    ctx.stage_ids = {}

    fake_db = AsyncMock()
    fake_doc = MagicMock(spec=Document)
    fake_doc.name = "agenda.pdf"
    fake_doc.source_metadata = {"school_id": "400", "school_org_code": "00010000"}
    fake_doc.source_type = "school_scraper"
    fake_doc.meeting_doc_type = None
    fake_doc.meeting_body = None
    fake_doc.document_quality = "clean_digital"
    fake_doc.state = "MA"
    fake_doc.district_name = "Agawam"
    fake_doc.school_year = None
    fake_doc.quarter_month = None
    fake_doc.meeting_date = None
    fake_db.get = AsyncMock(return_value=fake_doc)

    stage_record = MagicMock()
    stage_record.id = 1
    captured: list[dict] = []

    async def _capture_add(*, document_id, chunks, embeddings, metadatas):
        captured.extend(metadatas)
        return ["pid-1"]

    fake_vs = AsyncMock()
    fake_vs.delete_document = AsyncMock(return_value=True)
    fake_vs.delete_document_summary = AsyncMock(return_value=True)
    fake_vs.add_chunks_returning_ids = _capture_add

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
        asyncio.run(_step5_store_async(ctx, MagicMock()))

    assert captured[0]["school_id"] == 400
    assert isinstance(captured[0]["school_id"], int)


def test_qdrant_normalization_keeps_int_school_id():
    """add_chunks_returning_ids must keep int school_id (not drop as None)."""
    from app.services.vector_store.qdrant_store import QdrantStore

    with patch.object(QdrantStore, "__init__", lambda self, url=None: None):
        store = QdrantStore()
    store.client = MagicMock()
    store._get_or_create_collection = AsyncMock(return_value="justedtech_4")
    store._upsert_points_individually = MagicMock()
    store.client.upsert = MagicMock()

    point_ids = asyncio.run(
        store.add_chunks_returning_ids(
            document_id="school-02690000-abc",
            chunks=["hello"],
            embeddings=[[0.1, 0.2]],
            metadatas=[
                {
                    "tenant_id": 4,
                    "school_id": 399,
                    "district_name": "Abington",
                    "classified": False,
                    "action_stage": None,  # must be skipped
                }
            ],
        )
    )
    assert len(point_ids) == 1
    # Inspect the payload that was upserted.
    upsert_kwargs = store.client.upsert.call_args.kwargs
    points = upsert_kwargs["points"]
    payload = points[0]["payload"]
    assert payload["school_id"] == 399
    assert "action_stage" not in payload  # None values skipped
