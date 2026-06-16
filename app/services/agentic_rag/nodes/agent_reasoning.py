"""
Node 1 – agent_reasoning

Builds the LLM request from the current message history, calls the model
(ChatOpenAI with tools bound), and returns the AI response.

When `state.force_answer` is True, an extra human message is injected
instructing the model to produce a final answer without further tool calls.
"""

import logging
from typing import Any, Callable

from langchain_core.messages import HumanMessage
from langchain_core.runnables import RunnableConfig

from app.services.agentic_rag.state import AgentState

logger = logging.getLogger(__name__)

_FORCE_ANSWER_NUDGE = (
    "IMPORTANT: You have reached the maximum number of research steps. "
    "Provide your best analytical answer right now using the information "
    "you have already gathered.  Do not call any more tools."
)


def make_agent_reasoning_node(llm_with_tools: Any) -> Callable:
    """
    Factory that closes over the pre-built LLM+tools binding so the graph
    only initialises ChatOpenAI once.

    Args:
        llm_with_tools: A ChatOpenAI instance with `.bind_tools(AGENT_TOOLS)`
                        already applied.

    Returns:
        An async node function compatible with LangGraph's StateGraph.
    """

    async def agent_reasoning(
        state: AgentState, config: RunnableConfig
    ) -> dict[str, Any]:
        messages = list(state["messages"])

        if state.get("force_answer"):
            messages.append(HumanMessage(content=_FORCE_ANSWER_NUDGE))

        logger.debug(
            f"[agent_reasoning] invoking LLM with {len(messages)} messages "
            f"(iteration={state.get('iteration_count', 0)}, "
            f"force_answer={state.get('force_answer', False)})"
        )

        response = await llm_with_tools.ainvoke(messages, config=config)

        # Accumulate token usage across all reasoning turns.
        usage: dict = getattr(response, "usage_metadata", None) or {}
        current = state.get("token_usage") or {}
        new_usage = {
            "input_tokens": current.get("input_tokens", 0)
            + usage.get("input_tokens", 0),
            "output_tokens": current.get("output_tokens", 0)
            + usage.get("output_tokens", 0),
            "total_tokens": current.get("total_tokens", 0)
            + usage.get("total_tokens", 0),
        }

        return {"messages": [response], "token_usage": new_usage}

    return agent_reasoning
