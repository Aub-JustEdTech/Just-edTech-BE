"""
Per-chunk contextual augmentation for the heatmap ingest pipeline.

Implements Anthropic's "contextual retrieval" pattern (see plan: Heatmap
Ingest Metadata v1, A2 path A). For each chunk, one LLM call is made with
the full source document as reference, producing a short situating context
that is prepended to the chunk text before embedding.

Properties:
  - State-agnostic (the context prompt is generic).
  - Failure-degrades-gracefully: a failed call leaves `situating_context`
    unset on that chunk and embedding proceeds with raw chunk text.
  - Concurrency-bounded via a semaphore (HEATMAP_CONTEXT_MAX_CONCURRENCY).
  - Relies on OpenAI automatic prompt caching for cost: the full-doc prefix
    is identical across chunks in a single doc, so cached input tokens are
    ~50% cheaper and ~80% faster after the first call.

Output is written onto `ctx.chunk_metadatas[i]["situating_context"]` by the
calling pipeline task; the augmented text is built in step 4 as
`f"{situating_context}\n\n{chunk_text}"` and passed to the embedder.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from app.core.config import settings
from app.services.llm.client import (
    get_async_openai_client,
    get_llm_api_key,
    normalize_model_name,
)

logger = logging.getLogger(__name__)


_SYSTEM_PROMPT = """\
You are preparing chunks of a K-12 US school district document for retrieval.

For each chunk, write a 1-2 sentence situating context that:
- Identifies what this document is (e.g. board meeting minutes, agenda, policy)
- Locates this chunk within the document (e.g. which section, which motion)
- Adds anything an end user would need to interpret the chunk in isolation

Rules:
- Be concrete and grounded in the whole document. Do not speculate.
- Do not summarize the chunk itself; the chunk text follows.
- Do not include stance or sentiment (that is a separate V2 pass).
- Plain prose only. No markdown, no headers, no bullet lists.
- 1-2 sentences, max ~50 words.

Return ONLY a JSON object: {"context": "<your 1-2 sentence situating context>"}"""


def _user_prompt(full_doc_text: str, chunk_text: str) -> str:
    # Cap the full-doc reference so we stay within prompt caching + token
    # budgets. The contextual-retrieval reference is the whole document;
    # for very large minutes we cap at 16000 chars (~4k tokens) and rely
    # on the cache to amortize across chunks in the same doc.
    doc_ref = (full_doc_text or "")[:16000]
    return (
        "FULL DOCUMENT (for reference only — do not quote):\n"
        f"{doc_ref}\n\n"
        "SITUATE THIS CHUNK:\n"
        f"{chunk_text}"
    )


def _response_format_schema() -> dict[str, Any]:
    # JSON schema for structured output. Returned as a dict; the caller
    # passes it as `response_format`.
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "ChunkContext",
            "strict": True,
            "schema": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "context": {"type": "string"},
                },
                "required": ["context"],
            },
        },
    }


class Contextualizer:
    """Generates a situating context per chunk for contextual retrieval.

    One LLM call per chunk. Configurable via `HEATMAP_CONTEXT_*` settings.
    """

    def __init__(
        self,
        model: str | None = None,
        max_concurrency: int | None = None,
        timeout_s: float = 60.0,
    ):
        self._model = normalize_model_name(
            model or getattr(settings, "HEATMAP_CONTEXT_MODEL", "openai/gpt-4o-mini")
        )
        self._max_concurrency = int(
            max_concurrency
            if max_concurrency is not None
            else getattr(settings, "HEATMAP_CONTEXT_MAX_CONCURRENCY", 5)
        )
        get_llm_api_key()
        self._client = get_async_openai_client(timeout=timeout_s)

    async def augment_chunks(
        self,
        full_doc_text: str,
        chunks: list[str],
    ) -> list[str]:
        """Return one situating context per chunk (same length as `chunks`).

        Failures degrade gracefully: a failed call returns "" for that
        chunk so the embedder falls back to raw chunk text.
        """
        if not chunks:
            return []
        semaphore = asyncio.Semaphore(self._max_concurrency)
        contexts: list[str] = [""] * len(chunks)

        async def _one(i: int, chunk_text: str) -> None:
            async with semaphore:
                contexts[i] = await self._augment_one(full_doc_text, chunk_text)

        await asyncio.gather(*[_one(i, c) for i, c in enumerate(chunks)])
        return contexts

    async def _augment_one(
        self, full_doc_text: str, chunk_text: str
    ) -> str:
        try:
            response = await self._client.chat.completions.create(
                model=self._model,
                messages=[
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {
                        "role": "user",
                        "content": _user_prompt(full_doc_text, chunk_text),
                    },
                ],
                temperature=0,
                max_completion_tokens=120,
                response_format=_response_format_schema(),
            )
            raw = response.choices[0].message.content or "{}"
            payload = json.loads(raw)
            return (payload.get("context") or "").strip()
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Contextualizer call failed for a chunk; "
                "falling back to raw chunk text: %s",
                exc,
            )
            return ""
