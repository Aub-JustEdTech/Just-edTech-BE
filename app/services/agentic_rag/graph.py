"""
Agent graph — 5-node LangGraph StateGraph.

Graph topology
--------------

    START
      │
      ▼
  agent_reasoning ──── has tool_calls? ──► tool_executor
      ▲                                         │
      │                                    status_emitter
      │                                         │
      └────────────────────────────────── guard_rails
      │
  no tool_calls
      │
      ▼
  extract_citations
      │
      ▼
     END

Usage
-----
Build once at application startup:

    from app.services.agentic_rag.graph import build_agent_graph

    graph = build_agent_graph()

    async with AsyncPostgresSaver.from_conn_string(DB_URL) as checkpointer:
        compiled = graph.compile(checkpointer=checkpointer)
        result = await compiled.ainvoke(initial_state, config={...})
"""

import logging
from typing import Any

from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableConfig
from langchain_openai import ChatOpenAI
from langgraph.graph import END, START, StateGraph

from app.core.config import settings
from app.services.agentic_rag.nodes import (
    extract_citations_node,
    guard_rails_node,
    make_agent_reasoning_node,
    status_emitter_node,
    tool_executor_node,
)
from app.services.agentic_rag.state import AgentState
from app.services.agentic_rag.tools import AGENT_TOOLS

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Routing
# ---------------------------------------------------------------------------


def route_after_reasoning(state: AgentState) -> str:
    """
    If the last AIMessage contains tool_calls → execute them.
    Otherwise → extract citations and end.
    """
    # If guard_rails has already decided we must produce a final answer, do not
    # allow the model to call more tools even if it tried. This prevents
    # infinite research loops where the model ignores the "no more tools" nudge.
    if state.get("force_answer"):
        return "extract_citations"

    last = state["messages"][-1] if state["messages"] else None
    if isinstance(last, AIMessage) and last.tool_calls:
        return "tool_executor"
    return "extract_citations"


# ---------------------------------------------------------------------------
# Graph factory
# ---------------------------------------------------------------------------


def build_agent_graph(model: str = "gpt-4o") -> StateGraph:
    """
    Build the uncompiled StateGraph.

    The caller is responsible for compiling it (optionally with a checkpointer):

        compiled = build_agent_graph().compile(checkpointer=my_checkpointer)

    Args:
        model: OpenAI model name to use for agent reasoning.
               Defaults to 'gpt-4o' for strong tool-use performance.

    Returns:
        An uncompiled `StateGraph[AgentState]`.
    """
    llm = ChatOpenAI(
        model=model,
        temperature=0,
        api_key=settings.OPENAI_API_KEY,
    ).bind_tools(AGENT_TOOLS)

    agent_reasoning_node = make_agent_reasoning_node(llm)

    graph: StateGraph = StateGraph(AgentState)

    # Register nodes
    graph.add_node("agent_reasoning", agent_reasoning_node)
    graph.add_node("tool_executor", tool_executor_node)
    graph.add_node("status_emitter", status_emitter_node)
    graph.add_node("guard_rails", guard_rails_node)
    graph.add_node("extract_citations", extract_citations_node)

    # Entry point
    graph.add_edge(START, "agent_reasoning")

    # After reasoning: branch on whether the model called any tools
    graph.add_conditional_edges(
        "agent_reasoning",
        route_after_reasoning,
        {
            "tool_executor": "tool_executor",
            "extract_citations": "extract_citations",
        },
    )

    # Tool execution cycle
    graph.add_edge("tool_executor", "status_emitter")
    graph.add_edge("status_emitter", "guard_rails")
    graph.add_edge("guard_rails", "agent_reasoning")

    # Terminal node
    graph.add_edge("extract_citations", END)

    logger.debug(f"Agent graph built (model={model}, tools={[t.name for t in AGENT_TOOLS]})")
    return graph


def get_initial_state(
    query: str,
    system_prompt: str,
    tenant_id: int,
    chatbot_config_id: int,
    document_ids: list[int] | None = None,
) -> dict[str, Any]:
    """
    Convenience helper: build the initial state dict for `compiled.ainvoke()`.

    Args:
        query:             The user's question.
        system_prompt:     Agent system prompt (from prompts.py).
        tenant_id:         Tenant the query belongs to.
        chatbot_config_id: Chatbot configuration ID.
        document_ids:      Optional list of document IDs to restrict searches.

    Returns:
        A fully populated initial AgentState dict.
    """
    from langchain_core.messages import HumanMessage, SystemMessage

    return {
        "messages": [
            SystemMessage(content=system_prompt),
            HumanMessage(content=query),
        ],
        "tenant_id": tenant_id,
        "chatbot_config_id": chatbot_config_id,
        "document_ids": document_ids,
        "iteration_count": 0,
        "max_iterations": settings.AGENT_MAX_ITERATIONS,
        "force_answer": False,
        "status_updates": [],
        "citations": [],
        "token_usage": {},
    }
