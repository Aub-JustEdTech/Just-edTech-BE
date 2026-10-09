"""Unit tests for the Contextualizer (per-chunk contextual augmentation).

The Contextualizer makes one LLM call per chunk. We mock the OpenAI client
so these tests run without network access. Cover:
  - Augmented text built as `context + "\n\n" + chunk` is the embedder's job;
    here we verify the context strings are stashed per chunk in order.
  - Failed LLM calls degrade to "" (empty context) so embedder falls back.
  - Empty chunk list returns empty list.
  - Concurrency cap is respected (smoke: semaphore exists).

Run:
    poetry run pytest tests/test_contextualizer.py -v
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.heatmap_ingest import contextualizer as ctx_mod
from app.services.heatmap_ingest.contextualizer import Contextualizer


def _make_contextualizer(max_concurrency: int = 2) -> Contextualizer:
    """Construct a Contextualizer with mocked LLM client (no network)."""
    with patch.object(ctx_mod, "get_llm_api_key"), \
         patch.object(ctx_mod, "get_async_openai_client", return_value=MagicMock()):
        c = Contextualizer(max_concurrency=max_concurrency)
    return c


def _make_response(content: str) -> MagicMock:
    """Build a fake chat.completions.create response object."""
    msg = MagicMock()
    msg.content = content
    choice = MagicMock()
    choice.message = msg
    resp = MagicMock()
    resp.choices = [choice]
    return resp


@pytest.mark.asyncio
async def test_augment_chunks_returns_one_context_per_chunk_in_order():
    ctx = _make_contextualizer(max_concurrency=2)
    client = MagicMock()
    # Return a different context per call, in chunk order.
    responses = [
        _make_response(json.dumps({"context": f"context-for-chunk-{i}"}))
        for i in range(3)
    ]
    client.chat.completions.create = AsyncMock(side_effect=responses)
    ctx._client = client

    contexts = await ctx.augment_chunks(
        full_doc_text="FULL DOC", chunks=["c0", "c1", "c2"]
    )
    assert contexts == [
        "context-for-chunk-0",
        "context-for-chunk-1",
        "context-for-chunk-2",
    ]


@pytest.mark.asyncio
async def test_augment_chunks_failed_call_degrades_to_empty_string():
    ctx = _make_contextualizer(max_concurrency=1)
    client = MagicMock()
    client.chat.completions.create = AsyncMock(side_effect=RuntimeError("boom"))
    ctx._client = client

    contexts = await ctx.augment_chunks("FULL", ["chunk-a"])
    assert contexts == [""]


@pytest.mark.asyncio
async def test_augment_chunks_empty_input():
    ctx = _make_contextualizer()
    assert await ctx.augment_chunks("FULL", []) == []


@pytest.mark.asyncio
async def test_augment_chunks_non_json_response_degrades_to_empty():
    ctx = _make_contextualizer(max_concurrency=1)
    client = MagicMock()
    client.chat.completions.create = AsyncMock(
        return_value=_make_response("not json at all")
    )
    ctx._client = client

    contexts = await ctx.augment_chunks("FULL", ["c"])
    assert contexts == [""]


@pytest.mark.asyncio
async def test_augment_chunks_strips_whitespace_from_context():
    ctx = _make_contextualizer(max_concurrency=1)
    client = MagicMock()
    client.chat.completions.create = AsyncMock(
        return_value=_make_response(json.dumps({"context": "  padded  "}))
    )
    ctx._client = client

    contexts = await ctx.augment_chunks("FULL", ["c"])
    assert contexts == ["padded"]


@pytest.mark.asyncio
async def test_augment_chunks_respects_max_concurrency():
    # With concurrency=1, calls should be sequential. We verify by counting
    # in-flight calls at any moment never exceeds 1.
    ctx = _make_contextualizer(max_concurrency=1)
    client = MagicMock()
    in_flight = 0
    peak = 0

    async def _track(*args, **kwargs):
        nonlocal in_flight, peak
        in_flight += 1
        peak = max(peak, in_flight)
        await asyncio.sleep(0.01)
        in_flight -= 1
        return _make_response(json.dumps({"context": "x"}))

    client.chat.completions.create = AsyncMock(side_effect=_track)
    ctx._client = client

    await ctx.augment_chunks("FULL", ["c0", "c1", "c2", "c3"])
    assert peak == 1
