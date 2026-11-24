"""BatchClassifier.submit_pending_batch always uses OpenAI Batch APIs.

Even when LLM_API_PROVIDER=openrouter (chat/doc-classify), chunk submit
must go through OpenAI Files + Batches with a bare model id — never the
old OpenRouter inline chat path.

Run:
    poetry run pytest tests/test_batch_classifier_submit_openai_only.py -v
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.models.pending_classification import PendingClassification
from app.services.heatmap_ingest.batch_classifier import BatchClassifier
from app.services.llm.client import get_openai_batch_client, strip_model_provider_prefix


def test_strip_model_provider_prefix_for_batch_jsonl():
    assert strip_model_provider_prefix("openai/gpt-4o-mini") == "gpt-4o-mini"
    assert strip_model_provider_prefix("gpt-4o-mini") == "gpt-4o-mini"


def test_get_openai_batch_client_requires_openai_key(monkeypatch):
    monkeypatch.setattr(
        "app.services.llm.client.settings.OPENAI_API_KEY", None, raising=False
    )
    monkeypatch.setattr(
        "app.services.llm.client.settings.LLM_API_PROVIDER", "openrouter"
    )
    with pytest.raises(ValueError, match="OPENAI_API_KEY"):
        get_openai_batch_client()


def test_get_openai_batch_client_uses_openai_key_only(monkeypatch):
    monkeypatch.setattr(
        "app.services.llm.client.settings.OPENAI_API_KEY", "sk-test-openai"
    )
    monkeypatch.setattr(
        "app.services.llm.client.settings.LLM_API_PROVIDER", "openrouter"
    )
    monkeypatch.setattr(
        "app.services.llm.client.settings.OPENROUTER_API_KEY", "sk-or-test"
    )

    with patch("app.services.llm.client.AsyncOpenAI") as mock_cls:
        get_openai_batch_client()
        kwargs = mock_cls.call_args.kwargs
        assert kwargs["api_key"] == "sk-test-openai"
        assert "base_url" not in kwargs
        assert "default_headers" not in kwargs


@pytest.mark.asyncio
async def test_submit_pending_batch_uses_openai_batches_under_openrouter(
    monkeypatch,
):
    """Regression: openrouter must not divert submit to inline chat."""
    monkeypatch.setattr(
        "app.core.config.settings.LLM_API_PROVIDER", "openrouter"
    )
    monkeypatch.setattr(
        "app.core.config.settings.HEATMAP_INGEST_CHUNK_CLASSIFIER_MODEL",
        "openai/gpt-4o-mini",
    )
    monkeypatch.setattr(
        "app.core.config.settings.HEATMAP_INGEST_BATCH_SIZE", 50
    )
    monkeypatch.setattr(
        "app.core.config.settings.HEATMAP_INGEST_BATCH_MAX_BYTES",
        180 * 1024 * 1024,
    )

    row = PendingClassification(
        document_id=1,
        qdrant_point_id="p1",
        chunk_index=0,
        chunk_text="Board discussed curriculum adoption.",
        status="pending",
    )
    # id is used as Batch custom_id; set after construct for SQLAlchemy models.
    row.id = 42

    pending_result = MagicMock()
    pending_result.scalars.return_value.all.return_value = [row]
    db = MagicMock()
    db.execute = AsyncMock(return_value=pending_result)
    db.commit = AsyncMock()
    db.add = MagicMock()

    uploaded_jsonl: list[bytes] = []

    async def _fake_files_create(*, file, purpose):
        assert purpose == "batch"
        uploaded_jsonl.append(file[1].getvalue())
        return SimpleNamespace(id="file_test")

    batches_create = AsyncMock(
        return_value=SimpleNamespace(id="batch_test", status="validating")
    )

    classifier = BatchClassifier.__new__(BatchClassifier)
    classifier._model = strip_model_provider_prefix("openai/gpt-4o-mini")
    classifier._client = SimpleNamespace(
        files=SimpleNamespace(
            create=_fake_files_create,
            retrieve=AsyncMock(
                return_value=SimpleNamespace(status="processed")
            ),
        ),
        batches=SimpleNamespace(create=batches_create),
        chat=SimpleNamespace(
            completions=SimpleNamespace(
                create=AsyncMock(
                    side_effect=AssertionError(
                        "must not use chat.completions for batch submit"
                    )
                )
            )
        ),
    )
    classifier._s3 = SimpleNamespace(
        upload_file_object=AsyncMock(return_value="s3://bucket/key")
    )
    classifier._state_for_doc = AsyncMock(return_value=None)
    classifier._wait_for_file_processed = AsyncMock()

    assert not hasattr(classifier, "_submit_pending_via_direct_api")

    job = await classifier.submit_pending_batch(db)

    assert job is not None
    assert job.batch_id == "batch_test"
    batches_create.assert_awaited_once()
    assert row.status == "submitted"
    assert row.batch_id == "batch_test"

    assert uploaded_jsonl, "expected JSONL uploaded to OpenAI Files API"
    line = json.loads(uploaded_jsonl[0].decode("utf-8").strip().splitlines()[0])
    assert line["body"]["model"] == "gpt-4o-mini"
    assert "/" not in line["body"]["model"]
