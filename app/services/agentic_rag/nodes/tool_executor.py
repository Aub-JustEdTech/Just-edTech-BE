"""
Node 2 – tool_executor

Reads every tool_call on the latest AIMessage, executes each tool with the
graph's RunnableConfig (so tenant_id / chatbot_config_id are available via
InjectedToolArg), and appends ToolMessage results to state.messages.

Increments iteration_count after each full round of tool calls.
"""

import json
import logging
from typing import Any

from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.runnables import RunnableConfig

from app.services.agentic_rag.state import AgentState
from app.services.agentic_rag.tools import AGENT_TOOLS

logger = logging.getLogger(__name__)

# Build a name → tool lookup once at module load.
_TOOL_MAP: dict[str, Any] = {t.name: t for t in AGENT_TOOLS}


async def tool_executor_node(
    state: AgentState, config: RunnableConfig
) -> dict[str, Any]:
    """Execute all tool calls from the most recent AIMessage."""
    last_message = state["messages"][-1]

    if not isinstance(last_message, AIMessage) or not last_message.tool_calls:
        logger.warning("[tool_executor] called but last message has no tool_calls")
        return {"iteration_count": state.get("iteration_count", 0) + 1}

    tool_messages: list[ToolMessage] = []

    for tool_call in last_message.tool_calls:
        tool_name: str = tool_call["name"]
        tool_args: dict = tool_call["args"]
        tool_id: str = tool_call["id"]

        tool_fn = _TOOL_MAP.get(tool_name)
        if tool_fn is None:
            content = f"Error: unknown tool '{tool_name}'."
            logger.warning(f"[tool_executor] {content}")
        else:
            try:
                result = await tool_fn.ainvoke(tool_args, config=config)
                content = (
                    json.dumps(result, default=str)
                    if not isinstance(result, str)
                    else result
                )
                logger.debug(
                    f"[tool_executor] {tool_name} returned "
                    f"{len(result) if isinstance(result, list) else 1} result(s)"
                )
            except Exception as exc:
                content = f"Error executing {tool_name}: {exc}"
                logger.error(
                    f"[tool_executor] {tool_name} raised: {exc}", exc_info=True
                )

        tool_messages.append(
            ToolMessage(
                content=content,
                tool_call_id=tool_id,
                name=tool_name,
            )
        )

    return {
        "messages": tool_messages,
        "iteration_count": state.get("iteration_count", 0) + 1,
    }
