"""
AgenticRAGService — entry point for the analytical agent.

Public interface
---------------
    service = AgenticRAGService()
    response, citations, metadata = await service.process_query(
        db=db,
        query=query,
        conversation_history=recent_messages,
        tenant_id=tenant_id,
        chatbot_config_id=chatbot_config_id,
        conversation_id=conversation_id,   # used as LangGraph thread_id
        document_ids=None,
    )

The return signature is identical to `process_rag_query_with_citations` so
`ChatService` can swap implementations without touching anything downstream.

Checkpointing
-------------
Uses `AsyncPostgresSaver` (psycopg3) with the app's PostgreSQL connection
string.  Each conversation gets its own thread_id so the agent can remember
what it searched in previous turns within the same conversation.

If the checkpointer fails to initialise (e.g. network issue) the query still
runs — it falls back to a stateless (non-checkpointed) compiled graph.
"""

import logging
from typing import Any

from langchain_core.runnables import RunnableConfig
from langsmith import traceable
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.schemas.citations import CitationCreate
from app.services.agentic_rag.graph import build_agent_graph, get_initial_state
from app.services.agentic_rag.prompts import AGENT_SYSTEM_PROMPT

logger = logging.getLogger(__name__)


class AgenticRAGService:
    """
    Wraps the LangGraph agent graph and exposes a single async method that
    matches the signature of `process_rag_query_with_citations`.
    """

    def __init__(self, model: str = "gpt-4o"):
        self._graph = build_agent_graph(model=model)
        # Compiled graph without checkpointing — used as a fallback and cached
        # for requests that don't need conversation persistence.
        self._compiled_stateless = self._graph.compile()

    @traceable(name="agentic_rag_process_query")
    async def process_query(
        self,
        query: str,
        db: AsyncSession,
        conversation_history: list,
        tenant_id: int,
        chatbot_config_id: int,
        conversation_id: int | None = None,
        document_ids: list[int] | None = None,
    ) -> tuple[str, list[CitationCreate], dict[str, Any]]:
        """
        Run the agent and return (response_text, citations, metadata).

        Args:
            db:                  Async DB session (kept for interface parity;
                                 tools open their own sessions internally).
            query:               The user's question.
            conversation_history: Recent message objects (for parity with the
                                  legacy interface; the agent builds its own
                                  message window from state).
            tenant_id:           Tenant owning this conversation.
            chatbot_config_id:   Chatbot configuration ID.
            conversation_id:     Used as the LangGraph thread_id for
                                 checkpointing.  Pass None for stateless runs.
            document_ids:        Optional list of document IDs to scope search.

        Returns:
            Tuple of (response_text, List[CitationCreate], metadata_dict).
        """
        initial_state = get_initial_state(
            query=query,
            system_prompt=AGENT_SYSTEM_PROMPT,
            tenant_id=tenant_id,
            chatbot_config_id=chatbot_config_id,
            document_ids=document_ids,
        )

        # RunnableConfig carries tenant context into tools (via InjectedToolArg)
        # and the thread_id into the checkpointer.
        run_config: RunnableConfig = {
            "configurable": {
                "thread_id": str(conversation_id) if conversation_id else "stateless",
                "tenant_id": tenant_id,
                "chatbot_config_id": chatbot_config_id,
            },
            "recursion_limit": settings.AGENT_MAX_ITERATIONS * 3,
        }

        try:
            final_state = await self._run_with_checkpointing(
                initial_state, run_config, conversation_id
            )
        except Exception as exc:
            logger.error(
                f"[AgenticRAGService] Agent execution failed: {exc}", exc_info=True
            )
            return (
                "I encountered an error while researching your question. "
                "Please try again.",
                [],
                {"error": str(exc), "tokens_used": 0},
            )

        return self._extract_result(final_state)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _run_with_checkpointing(
        self,
        initial_state: dict,
        run_config: RunnableConfig,
        conversation_id: int | None,
    ) -> dict:
        """
        Try to run the graph with PostgreSQL checkpointing; fall back to the
        stateless compiled graph if the checkpointer cannot be set up.
        """
        if conversation_id is None:
            return await self._compiled_stateless.ainvoke(
                initial_state, config=run_config
            )

        try:
            from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

            async with AsyncPostgresSaver.from_conn_string(
                settings.DATABASE_URL
            ) as checkpointer:
                await checkpointer.setup()
                compiled = self._graph.compile(checkpointer=checkpointer)
                return await compiled.ainvoke(initial_state, config=run_config)

        except Exception as exc:
            logger.warning(
                f"[AgenticRAGService] Checkpointer unavailable "
                f"({exc}); running stateless.",
                exc_info=True,
            )
            return await self._compiled_stateless.ainvoke(
                initial_state, config=run_config
            )

    @staticmethod
    def _extract_result(
        state: dict,
    ) -> tuple[str, list[CitationCreate], dict[str, Any]]:
        """Pull response text, citations, and metadata from the final state."""
        from langchain_core.messages import AIMessage

        # Last AI message = final answer
        response_text = ""
        for msg in reversed(state.get("messages", [])):
            if isinstance(msg, AIMessage) and msg.content:
                response_text = (
                    msg.content
                    if isinstance(msg.content, str)
                    else str(msg.content)
                )
                break

        if not response_text:
            response_text = "I was unable to produce an answer from the available documents."

        # Build CitationCreate objects
        raw_citations: list[dict] = state.get("citations") or []
        citations = []
        for i, c in enumerate(raw_citations):
            citations.append(
                CitationCreate(
                    document_title=c.get("document_title", ""),
                    document_url=c.get("document_url", "#"),
                    snippet=c.get("snippet", ""),
                    position=c.get("position", i + 1),
                    page_number=c.get("page_number"),
                )
            )

        # Metadata — match the shape expected by ChatService
        token_usage: dict = state.get("token_usage") or {}
        metadata: dict[str, Any] = {
            "tokens_used": token_usage.get("total_tokens", 0),
            "input_tokens": token_usage.get("input_tokens", 0),
            "output_tokens": token_usage.get("output_tokens", 0),
            "chunks_retrieved": len(raw_citations),
            "model": "gpt-4o",
            "agent_iterations": state.get("iteration_count", 0),
            "status_updates": state.get("status_updates") or [],
        }

        return response_text, citations, metadata


# Singleton — build once at import time so the graph and LLM binding are
# reused across requests.
agentic_rag_service = AgenticRAGService()
