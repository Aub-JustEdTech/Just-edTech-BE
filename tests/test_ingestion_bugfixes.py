"""Regression tests for the bugs fixed on feat_bulk-scraped-media-ingestion.

Each test pins down the exact failure mode described in
docs/SCRAPED_MEDIA_INGESTION_CHANGES.md so a future refactor can't silently
reintroduce it. All pure-function / mocked-boundary tests -- no DB, no
network, no OpenAI/Qdrant calls.

Run:
    poetry run pytest tests/test_ingestion_bugfixes.py -v
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core.config import settings
from app.services.heatmap_ingest.prompt import build_batch_request_line
from app.services.llm.client import normalize_model_name

# ---------------------------------------------------------------------------
# Bug: normalize_model_name() didn't strip the OpenRouter prefix under
# direct OpenAI (change log #4) -- broke every direct-OpenAI call, including
# live chat, once LLM_API_PROVIDER=openai.
# ---------------------------------------------------------------------------


class TestNormalizeModelName:
    def test_strips_prefix_under_direct_openai(self, monkeypatch):
        monkeypatch.setattr(settings, "LLM_API_PROVIDER", "openai")
        assert normalize_model_name("openai/gpt-4o-mini") == "gpt-4o-mini"

    def test_leaves_bare_name_alone_under_direct_openai(self, monkeypatch):
        monkeypatch.setattr(settings, "LLM_API_PROVIDER", "openai")
        assert normalize_model_name("gpt-4o-mini") == "gpt-4o-mini"

    def test_adds_prefix_under_openrouter_when_missing(self, monkeypatch):
        monkeypatch.setattr(settings, "LLM_API_PROVIDER", "openrouter")
        assert normalize_model_name("gpt-4o-mini") == "openai/gpt-4o-mini"

    def test_leaves_prefixed_name_alone_under_openrouter(self, monkeypatch):
        monkeypatch.setattr(settings, "LLM_API_PROVIDER", "openrouter")
        assert normalize_model_name("openai/gpt-4o-mini") == "openai/gpt-4o-mini"

    def test_empty_string_is_a_noop(self, monkeypatch):
        monkeypatch.setattr(settings, "LLM_API_PROVIDER", "openai")
        assert normalize_model_name("") == ""


# ---------------------------------------------------------------------------
# Bug: build_batch_request_line() sent "method": "post" (lowercase) -- every
# line in the first real Batch API submission failed validation (change
# log #6).
# ---------------------------------------------------------------------------


def test_batch_request_line_uses_uppercase_post_method():
    line = build_batch_request_line(
        custom_id="1",
        chunk_text="Some board meeting minutes about the budget.",
        model="gpt-4o-mini",
        state="MA",
    )

    assert line["method"] == "POST"
    assert line["method"] != "post"


# ---------------------------------------------------------------------------
# Bug: QdrantStore.add_chunks_returning_ids sent all chunks in one upsert
# call and swallowed write failures into an empty list, reporting false
# success (change log #5). Fix batches upserts and lets failures raise.
# ---------------------------------------------------------------------------


class TestQdrantUpsertBatchingAndRaising:
    @pytest.fixture
    def store(self, monkeypatch):
        from app.services.vector_store.qdrant_store import QdrantStore

        instance = QdrantStore.__new__(QdrantStore)  # skip network-touching __init__
        instance.client = MagicMock()
        monkeypatch.setattr(
            instance, "_get_or_create_collection", AsyncMock(return_value="coll_2")
        )
        return instance

    async def _add(self, store, n_chunks: int):
        chunks = [f"chunk {i}" for i in range(n_chunks)]
        embeddings = [[0.1, 0.2] for _ in range(n_chunks)]
        metadatas = [{"tenant_id": 2} for _ in range(n_chunks)]
        return await store.add_chunks_returning_ids(
            document_id="doc-1",
            chunks=chunks,
            embeddings=embeddings,
            metadatas=metadatas,
        )

    async def test_upserts_are_split_into_batches(self, store, monkeypatch):
        monkeypatch.setattr(settings, "QDRANT_UPSERT_BATCH_SIZE", 2)
        # 5 chunks / batch size 2 -> 3 upsert calls (2, 2, 1), covering a
        # small 2-3-district-sized document rather than the full 342-chunk
        # doc from the real incident.
        point_ids = await self._add(store, n_chunks=5)

        assert len(point_ids) == 5
        assert store.client.upsert.call_count == 3
        batch_sizes = [
            len(call.kwargs["points"]) for call in store.client.upsert.call_args_list
        ]
        assert batch_sizes == [2, 2, 1]

    async def test_failed_upsert_raises_instead_of_returning_empty_list(
        self, store, monkeypatch
    ):
        monkeypatch.setattr(settings, "QDRANT_UPSERT_BATCH_SIZE", 100)
        store.client.upsert.side_effect = RuntimeError("write timeout")

        with pytest.raises(RuntimeError, match="write timeout"):
            await self._add(store, n_chunks=3)


# ---------------------------------------------------------------------------
# Bug: QdrantStore.update_metadata swallowed failures and returned False,
# but its callers never checked the boolean -- a failed classification
# write still got marked 'applied' (change log #9).
# ---------------------------------------------------------------------------


class TestQdrantUpdateMetadataRaises:
    @pytest.fixture
    def store(self):
        from app.services.vector_store.qdrant_store import QdrantStore

        instance = QdrantStore.__new__(QdrantStore)
        instance.client = MagicMock()
        return instance

    async def test_set_payload_failure_raises(self, store):
        store.client.retrieve.return_value = [
            MagicMock(id="p1", payload={"tenant_id": 2})
        ]
        store.client.set_payload.side_effect = RuntimeError("qdrant unavailable")

        with pytest.raises(RuntimeError, match="qdrant unavailable"):
            await store.update_metadata(
                chunk_ids=["p1"], metadata={"classified": True}, tenant_id=2
            )
