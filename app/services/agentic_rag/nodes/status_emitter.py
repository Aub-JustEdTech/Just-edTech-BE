"""
Node 3 – status_emitter

Inspects the tool calls that were just executed and appends a human-readable
status string to state.status_updates.  These strings are surfaced to the
client as SSE "status" events while the agent is thinking.
"""

import logging
from typing import Any

from langchain_core.messages import AIMessage

from app.services.agentic_rag.state import AgentState

logger = logging.getLogger(__name__)


def _make_status(tool_name: str, args: dict) -> str:
    """Produce a short, friendly progress description for a tool call."""
    if tool_name == "search_knowledge_base":
        query = args.get("query", "")[:60]
        return f"Searching documents for '{query}'…"
    if tool_name == "find_relevant_documents":
        query = args.get("query", "")[:60]
        return f"Looking for documents about '{query}'…"
    if tool_name == "list_documents":
        category = args.get("category", "")
        suffix = f" ({category})" if category else ""
        return f"Listing available documents{suffix}…"
    if tool_name == "search_tables":
        query = args.get("query", "")[:60]
        return f"Searching financial/tabular data for '{query}'…"
    if tool_name == "get_document_details":
        doc_id = args.get("document_id", "")
        return f"Examining document {doc_id}…"
    return f"Running {tool_name}…"


async def status_emitter_node(state: AgentState) -> dict[str, Any]:
    """Append status strings for the tool calls in the last AIMessage."""
    # Walk backwards to find the most recent AIMessage that had tool_calls.
    last_ai: AIMessage | None = None
    for msg in reversed(state["messages"]):
        if isinstance(msg, AIMessage) and msg.tool_calls:
            last_ai = msg
            break

    if last_ai is None:
        return {}

    current_updates: list[str] = list(state.get("status_updates") or [])
    for tc in last_ai.tool_calls:
        status = _make_status(tc["name"], tc.get("args", {}))
        current_updates.append(status)
        logger.debug(f"[status_emitter] {status}")

    return {"status_updates": current_updates}
