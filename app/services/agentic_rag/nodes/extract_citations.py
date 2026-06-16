"""
Node 5 – extract_citations

Runs after the agent produces its final answer.  Scans all ToolMessage
results from search_knowledge_base and search_tables, deduplicates by
document_id, and builds CitationCreate-compatible dicts stored in
state.citations.

The graph ends immediately after this node.
"""

import json
import logging
from typing import Any

from langchain_core.messages import ToolMessage

from app.services.agentic_rag.state import AgentState

logger = logging.getLogger(__name__)

# Only these tools produce chunk results worth citing.
_SEARCH_TOOLS = {"search_knowledge_base", "search_tables"}


async def extract_citations_node(state: AgentState) -> dict[str, Any]:
    """Build deduplicated citations from all search tool results."""
    # document_name → best citation dict (dedup by document name)
    seen: dict[str, dict] = {}
    # Track the best score we have seen per document key so that for documents
    # with multiple chunks we keep the strongest match rather than the first.
    best_scores: dict[str, float] = {}

    for msg in state["messages"]:
        if not isinstance(msg, ToolMessage):
            continue
        tool_name = getattr(msg, "name", None)
        if tool_name not in _SEARCH_TOOLS:
            continue

        try:
            results: list[dict] = json.loads(msg.content)
        except (json.JSONDecodeError, TypeError, ValueError):
            continue

        if not isinstance(results, list):
            continue

        for chunk in results:
            doc_name: str = chunk.get("document_name", "")
            # Prefer the integer DB ID when available so that downstream code
            # can look up the Document row and generate a presigned S3 URL.
            db_id = chunk.get("document_db_id")
            doc_uuid: str = str(chunk.get("document_id", ""))

            key = doc_name or str(db_id) or doc_uuid
            if not key:
                continue

            snippet = chunk.get("text", "")
            score = float(chunk.get("score", 0.0) or 0.0)

            # If we've already seen this document, only replace the citation if
            # this chunk has a higher relevance score. This works together with
            # the lexical-aware re-ranking in `search_knowledge_base` so that
            # FAQ-style exact-match chunks are preferred.
            if key in seen and key in best_scores and score <= best_scores[key]:
                continue

            best_scores[key] = score
            seen[key] = {
                "document_title": doc_name,
                "document_url": f"/documents/{db_id}" if db_id else "#",
                "snippet": snippet[:500] + ("…" if len(snippet) > 500 else ""),
                "position": len(seen) + 1,
                # Carry page_number through so the API layer can append
                # `#page={page_number}` to presigned PDF URLs, matching the
                # classic RAG behaviour.
                "page_number": chunk.get("page_number"),
            }

    citations = list(seen.values())
    search_msg_count = sum(
        1
        for m in state["messages"]
        if isinstance(m, ToolMessage) and getattr(m, "name", None) in _SEARCH_TOOLS
    )
    logger.info(
        "[extract_citations] Built %d citation(s) from %d search tool messages.",
        len(citations),
        search_msg_count,
    )
    return {"citations": citations}
