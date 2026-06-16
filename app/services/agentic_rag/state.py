"""
AgentState — the single shared state that flows through every node of the
agent graph.

The `messages` field uses LangGraph's `add_messages` reducer so that each node
only needs to return the *new* messages it wants to append; existing messages
are preserved automatically.

All other fields are plain values; the last write wins for each key.
"""

from typing import Annotated, TypedDict

from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages


class AgentState(TypedDict):
    # ------------------------------------------------------------------ #
    # Conversation messages (system + human + AI + tool results)          #
    # add_messages reducer appends instead of replacing.                  #
    # ------------------------------------------------------------------ #
    messages: Annotated[list[BaseMessage], add_messages]

    # ------------------------------------------------------------------ #
    # Invocation context — set once when the graph is invoked             #
    # ------------------------------------------------------------------ #
    tenant_id: int
    chatbot_config_id: int
    document_ids: list[int] | None  # Optional document scoping

    # ------------------------------------------------------------------ #
    # Execution control                                                    #
    # ------------------------------------------------------------------ #
    iteration_count: int   # Incremented by tool_executor each cycle
    max_iterations: int    # Ceiling; defaults to settings.AGENT_MAX_ITERATIONS
    force_answer: bool     # Set by guard_rails when limits are hit

    # ------------------------------------------------------------------ #
    # Streaming / observability                                            #
    # ------------------------------------------------------------------ #
    status_updates: list[str]  # Human-readable progress messages

    # ------------------------------------------------------------------ #
    # Output                                                               #
    # ------------------------------------------------------------------ #
    citations: list[dict]  # Populated by extract_citations node
    token_usage: dict      # Accumulated across all agent_reasoning calls
