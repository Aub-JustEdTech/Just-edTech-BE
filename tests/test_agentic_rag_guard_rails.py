"""Tests for agentic RAG guard_rails termination reasons."""

from __future__ import annotations

import pytest

from app.services.agentic_rag.nodes.guard_rails import guard_rails_node
from app.services.agentic_rag.service import AgenticRAGService


@pytest.mark.asyncio
async def test_guard_rails_sets_max_iterations_reason(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.services.agentic_rag.nodes.guard_rails.settings.AGENT_MAX_ITERATIONS",
        3,
    )
    monkeypatch.setattr(
        "app.services.agentic_rag.nodes.guard_rails.settings.AGENT_MAX_TOKENS_BUDGET",
        120_000,
    )
    state = {
        "iteration_count": 3,
        "max_iterations": 3,
        "token_usage": {"total_tokens": 100},
        "force_answer": False,
        "termination_reason": None,
    }

    result = await guard_rails_node(state)  # type: ignore[arg-type]

    assert result["force_answer"] is True
    assert result["termination_reason"] == "max_iterations"


@pytest.mark.asyncio
async def test_guard_rails_sets_token_budget_reason(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.services.agentic_rag.nodes.guard_rails.settings.AGENT_MAX_ITERATIONS",
        12,
    )
    monkeypatch.setattr(
        "app.services.agentic_rag.nodes.guard_rails.settings.AGENT_MAX_TOKENS_BUDGET",
        1_000,
    )
    state = {
        "iteration_count": 1,
        "max_iterations": 12,
        "token_usage": {"total_tokens": 1_500},
        "force_answer": False,
        "termination_reason": None,
    }

    result = await guard_rails_node(state)  # type: ignore[arg-type]

    assert result["force_answer"] is True
    assert result["termination_reason"] == "token_budget"


@pytest.mark.asyncio
async def test_guard_rails_noop_under_limits(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.services.agentic_rag.nodes.guard_rails.settings.AGENT_MAX_ITERATIONS",
        12,
    )
    monkeypatch.setattr(
        "app.services.agentic_rag.nodes.guard_rails.settings.AGENT_MAX_TOKENS_BUDGET",
        120_000,
    )
    state = {
        "iteration_count": 2,
        "max_iterations": 12,
        "token_usage": {"total_tokens": 50},
    }

    result = await guard_rails_node(state)  # type: ignore[arg-type]
    assert result == {}


def test_extract_result_includes_termination_reason() -> None:
    from langchain_core.messages import AIMessage

    state = {
        "messages": [AIMessage(content="Partial answer from gathered evidence.")],
        "citations": [
            {
                "document_title": "Doc",
                "document_url": "/documents/1",
                "snippet": "Evidence",
                "position": 1,
                "page_number": 1,
            }
        ],
        "token_usage": {
            "total_tokens": 200,
            "input_tokens": 150,
            "output_tokens": 50,
        },
        "iteration_count": 12,
        "termination_reason": "max_iterations",
        "status_updates": ["Searching documents…"],
    }

    text, citations, metadata = AgenticRAGService._extract_result(state)

    assert "Partial answer" in text
    assert len(citations) == 1
    assert metadata["agent_iterations"] == 12
    assert metadata["termination_reason"] == "max_iterations"
