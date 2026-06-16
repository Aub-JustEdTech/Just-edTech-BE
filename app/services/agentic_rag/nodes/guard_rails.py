"""
Node 4 – guard_rails

Checks whether the agent has exceeded its resource limits and, if so, sets
force_answer=True so agent_reasoning will produce a final answer on its next
turn instead of calling more tools.

The graph does NOT terminate here — the agent always gets one last chance to
synthesise an answer from what it has gathered so far.
"""

import logging
from typing import Any

from app.core.config import settings
from app.services.agentic_rag.state import AgentState

logger = logging.getLogger(__name__)


async def guard_rails_node(state: AgentState) -> dict[str, Any]:
    """Check iteration and token limits; set force_answer if exceeded."""
    iteration_count: int = state.get("iteration_count", 0)
    max_iterations: int = state.get("max_iterations") or settings.AGENT_MAX_ITERATIONS

    token_usage: dict = state.get("token_usage") or {}
    total_tokens: int = token_usage.get("total_tokens", 0)
    token_budget: int = settings.AGENT_MAX_TOKENS_BUDGET

    hit_iteration_limit = iteration_count >= max_iterations
    hit_token_limit = total_tokens >= token_budget

    if hit_iteration_limit or hit_token_limit:
        reason = (
            f"iteration_count={iteration_count}>={max_iterations}"
            if hit_iteration_limit
            else f"total_tokens={total_tokens}>={token_budget}"
        )
        logger.info(f"[guard_rails] Limit reached ({reason}); forcing final answer.")
        return {"force_answer": True}

    return {}
