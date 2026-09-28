"""
Node 5 – extract_citations

Runs after the agent produces its final answer.  Scans all ToolMessage
results from search_knowledge_base, search_tables, and
get_district_citations, resolves each chunk to a Document DB id, and
builds CitationCreate-compatible dicts with ``document_url`` set to
``/documents/{id}`` (never scrape ``source_media_url`` /
``source_page_url``). The API layer upgrades those paths to presigned
S3 document URLs.

The graph ends immediately after this node.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from langchain_core.messages import ToolMessage
from sqlalchemy import select

from app.db.connector import AsyncSessionLocal
from app.models.documents import Document
from app.services.agentic_rag.state import AgentState

logger = logging.getLogger(__name__)

# Tools that return citeable chunk / document evidence.
# `get_district_citations` is the drill-down used after
# `count_districts_by_topic` — without it, cross-district answers
# would surface districts with empty UI citations.
_SEARCH_TOOLS = {
    "search_knowledge_base",
    "search_tables",
    "get_district_citations",
}


def _iter_chunks(tool_name: str, payload: Any) -> list[dict[str, Any]]:
    """Normalise tool payloads into a flat list of chunk-like dicts."""
    if tool_name == "get_district_citations":
        if isinstance(payload, dict):
            citations = payload.get("citations") or []
            return citations if isinstance(citations, list) else []
        return []

    if isinstance(payload, list):
        return [c for c in payload if isinstance(c, dict)]
    return []


def _chunk_snippet(chunk: dict[str, Any]) -> str:
    """Prefer `text` (search tools); fall back to `snippet` (district citations)."""
    text = chunk.get("text") or chunk.get("snippet") or ""
    return text if isinstance(text, str) else str(text)


def _coerce_db_id(value: Any) -> int | None:
    if value is None or value is False:
        return None
    try:
        db_id = int(value)
    except (TypeError, ValueError):
        return None
    return db_id if db_id > 0 else None


def _collect_raw_chunks(messages: list[Any]) -> list[dict[str, Any]]:
    chunks: list[dict[str, Any]] = []
    for msg in messages:
        if not isinstance(msg, ToolMessage):
            continue
        tool_name = getattr(msg, "name", None)
        if tool_name not in _SEARCH_TOOLS:
            continue
        try:
            payload = json.loads(msg.content)
        except (json.JSONDecodeError, TypeError, ValueError):
            continue
        for chunk in _iter_chunks(tool_name, payload):
            if isinstance(chunk, dict):
                chunks.append(chunk)
    return chunks


async def _resolve_missing_db_ids(
    tenant_id: int | None,
    chunks: list[dict[str, Any]],
) -> dict[str, int]:
    """Map document UUID → integer DB id for chunks missing ``document_db_id``.

    Citations must point at Documents (``/documents/{id}``), not scrape
    resource URLs. When tools only return the vector-store UUID, resolve
    it here so the API can attach a presigned document URL.
    """
    missing_uuids: set[str] = set()
    for chunk in chunks:
        if _coerce_db_id(chunk.get("document_db_id")) is not None:
            continue
        doc_uuid = chunk.get("document_id")
        if doc_uuid:
            missing_uuids.add(str(doc_uuid))

    if not missing_uuids or tenant_id is None:
        return {}

    try:
        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(Document.doc_id, Document.id).where(
                    Document.doc_id.in_(missing_uuids),
                    Document.tenant_id == tenant_id,
                )
            )
            return {str(doc_uuid): int(db_id) for doc_uuid, db_id in result.all()}
    except Exception:
        logger.warning(
            "[extract_citations] Failed to resolve document UUIDs to DB IDs",
            exc_info=True,
        )
        return {}


def _build_citations(
    chunks: list[dict[str, Any]],
    uuid_to_db_id: dict[str, int],
) -> list[dict[str, Any]]:
    """Deduplicate by Document DB id; emit only ``/documents/{id}`` URLs."""
    seen: dict[int, dict[str, Any]] = {}
    best_scores: dict[int, float] = {}

    for chunk in chunks:
        db_id = _coerce_db_id(chunk.get("document_db_id"))
        if db_id is None:
            doc_uuid = chunk.get("document_id")
            if doc_uuid:
                db_id = uuid_to_db_id.get(str(doc_uuid))
        if db_id is None:
            # No Document link possible — skip rather than emit a scrape
            # resource URL or a dead ``#`` placeholder.
            continue

        doc_name: str = (
            chunk.get("document_name") or chunk.get("document_title") or ""
        )
        snippet = _chunk_snippet(chunk)
        score = float(chunk.get("score", 0.0) or 0.0)

        if db_id in seen and score <= best_scores.get(db_id, 0.0):
            continue

        best_scores[db_id] = score
        seen[db_id] = {
            "document_title": doc_name,
            # Document URL only — never source_media_url / source_page_url.
            "document_url": f"/documents/{db_id}",
            "snippet": snippet[:500] + ("…" if len(snippet) > 500 else ""),
            "position": 0,  # renumbered below
            "page_number": chunk.get("page_number"),
        }

    citations = list(seen.values())
    for i, citation in enumerate(citations, start=1):
        citation["position"] = i
    return citations


async def extract_citations_node(state: AgentState) -> dict[str, Any]:
    """Build Document-linked citations from all search / citation tool results."""
    chunks = _collect_raw_chunks(state["messages"])
    uuid_to_db_id = await _resolve_missing_db_ids(state.get("tenant_id"), chunks)
    citations = _build_citations(chunks, uuid_to_db_id)

    search_msg_count = sum(
        1
        for m in state["messages"]
        if isinstance(m, ToolMessage) and getattr(m, "name", None) in _SEARCH_TOOLS
    )
    logger.info(
        "[extract_citations] Built %d citation(s) from %d search tool messages "
        "(%d raw chunk(s)).",
        len(citations),
        search_msg_count,
        len(chunks),
    )
    return {"citations": citations}
