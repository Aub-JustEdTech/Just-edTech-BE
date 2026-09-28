"""Tests for agentic RAG citation extraction.

Covers:
- Cross-district answers using ``get_district_citations`` must produce
  UI citations (previously only ``search_knowledge_base`` /
  ``search_tables`` were scanned).
- Citation ``document_url`` must be a Document path
  (``/documents/{id}``), never scrape ``source_media_url`` /
  ``source_page_url``.
- Chunks that only carry a vector-store UUID are resolved to a DB id.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.messages import AIMessage, ToolMessage

from app.services.agentic_rag.nodes.extract_citations import extract_citations_node


@pytest.mark.asyncio
async def test_extract_citations_from_search_knowledge_base() -> None:
    state = {
        "tenant_id": 1,
        "messages": [
            ToolMessage(
                name="search_knowledge_base",
                content=json.dumps(
                    [
                        {
                            "text": "Public comment supportive of LGBTQ rights.",
                            "document_name": "Quabbin Minutes 2025-03-12",
                            "document_id": "uuid-1",
                            "document_db_id": 101,
                            "page_number": 4,
                            "score": 0.91,
                            # Must NEVER become document_url
                            "source_media_url": "https://scrape.example/resource.pdf",
                        }
                    ]
                ),
                tool_call_id="call-1",
            ),
            AIMessage(content="Quabbin had supportive public comments."),
        ]
    }

    result = await extract_citations_node(state)  # type: ignore[arg-type]

    assert len(result["citations"]) == 1
    citation = result["citations"][0]
    assert citation["document_title"] == "Quabbin Minutes 2025-03-12"
    assert citation["document_url"] == "/documents/101"
    assert "scrape.example" not in citation["document_url"]
    assert citation["page_number"] == 4
    assert "Public comment supportive" in citation["snippet"]
    assert citation["position"] == 1


@pytest.mark.asyncio
async def test_extract_citations_from_get_district_citations() -> None:
    """District drill-down results must become Document-linked citations."""
    state = {
        "tenant_id": 1,
        "messages": [
            ToolMessage(
                name="count_districts_by_topic",
                content=json.dumps(
                    {
                        "districts": [
                            {
                                "district_name": "Quabbin Public School District",
                                "org_code": "QUABBIN",
                                "chunk_count": 3,
                            }
                        ]
                    }
                ),
                tool_call_id="call-count",
            ),
            ToolMessage(
                name="get_district_citations",
                content=json.dumps(
                    {
                        "org_code": "QUABBIN",
                        "district_name": "Quabbin Public School District",
                        "citations": [
                            {
                                "document_id": "uuid-q",
                                "document_db_id": 42,
                                "document_name": "Quabbin Board Minutes 2025-11-05",
                                "meeting_date": "2025-11-05",
                                "page_number": 12,
                                "snippet": "Commenter asked the district to affirm LGBTQ rights.",
                                "source_media_url": "https://district.example/resource.pdf",
                                "source_page_url": "https://district.example/meetings",
                            }
                        ],
                        "total": 1,
                    }
                ),
                tool_call_id="call-cite",
            ),
            AIMessage(
                content=(
                    "Quabbin Public School District had public comments "
                    "supportive of LGBTQ rights (Quabbin Board Minutes "
                    "2025-11-05, p. 12)."
                )
            ),
        ]
    }

    result = await extract_citations_node(state)  # type: ignore[arg-type]

    assert len(result["citations"]) == 1
    citation = result["citations"][0]
    assert citation["document_title"] == "Quabbin Board Minutes 2025-11-05"
    assert citation["document_url"] == "/documents/42"
    assert "district.example" not in citation["document_url"]
    assert citation["page_number"] == 12
    assert "affirm LGBTQ rights" in citation["snippet"]


@pytest.mark.asyncio
async def test_extract_citations_ignores_count_only_messages() -> None:
    """Counts without citation drill-down must not invent citations."""
    state = {
        "tenant_id": 1,
        "messages": [
            ToolMessage(
                name="count_districts_by_topic",
                content=json.dumps(
                    {
                        "districts": [
                            {
                                "district_name": "Ware Public School District",
                                "org_code": "WARE",
                                "chunk_count": 2,
                            }
                        ]
                    }
                ),
                tool_call_id="call-count",
            ),
            AIMessage(
                content="Ware Public School District appeared in the counts."
            ),
        ]
    }

    result = await extract_citations_node(state)  # type: ignore[arg-type]
    assert result["citations"] == []


@pytest.mark.asyncio
async def test_extract_citations_dedups_across_search_and_district_tools() -> None:
    state = {
        "tenant_id": 1,
        "messages": [
            ToolMessage(
                name="get_district_citations",
                content=json.dumps(
                    {
                        "citations": [
                            {
                                "document_db_id": 7,
                                "document_name": "Shared Doc",
                                "snippet": "Weaker district snippet.",
                                "page_number": 1,
                            }
                        ]
                    }
                ),
                tool_call_id="call-d",
            ),
            ToolMessage(
                name="search_knowledge_base",
                content=json.dumps(
                    [
                        {
                            "document_db_id": 7,
                            "document_name": "Shared Doc",
                            "text": "Stronger search hit with more context.",
                            "page_number": 3,
                            "score": 0.95,
                        }
                    ]
                ),
                tool_call_id="call-s",
            ),
        ]
    }

    result = await extract_citations_node(state)  # type: ignore[arg-type]

    assert len(result["citations"]) == 1
    citation = result["citations"][0]
    assert citation["document_url"] == "/documents/7"
    assert citation["page_number"] == 3
    assert "Stronger search hit" in citation["snippet"]


@pytest.mark.asyncio
async def test_extract_citations_resolves_uuid_to_document_db_id() -> None:
    """Chunks with only a vector UUID still get a Document URL."""
    state = {
        "tenant_id": 4,
        "messages": [
            ToolMessage(
                name="search_tables",
                content=json.dumps(
                    [
                        {
                            "text": "Budget line for curriculum materials.",
                            "document_name": "FY26 Budget",
                            "document_id": "uuid-budget",
                            "page_number": None,
                            "score": 0.8,
                            "source_media_url": "https://box.example/resource.xlsx",
                        }
                    ]
                ),
                tool_call_id="call-t",
            ),
        ]
    }

    mock_result = MagicMock()
    mock_result.all.return_value = [("uuid-budget", 55)]
    mock_db = AsyncMock()
    mock_db.execute = AsyncMock(return_value=mock_result)
    mock_session = MagicMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_db)
    mock_session.__aexit__ = AsyncMock(return_value=None)

    with patch(
        "app.services.agentic_rag.nodes.extract_citations.AsyncSessionLocal",
        return_value=mock_session,
    ):
        result = await extract_citations_node(state)  # type: ignore[arg-type]

    assert len(result["citations"]) == 1
    assert result["citations"][0]["document_url"] == "/documents/55"
    assert "box.example" not in result["citations"][0]["document_url"]


@pytest.mark.asyncio
async def test_extract_citations_skips_chunks_without_resolvable_document() -> None:
    """No Document id and no resolvable UUID → no citation (no resource URL fallback)."""
    state = {
        "tenant_id": 1,
        "messages": [
            ToolMessage(
                name="search_knowledge_base",
                content=json.dumps(
                    [
                        {
                            "text": "Orphan chunk",
                            "document_name": "Unknown",
                            "source_media_url": "https://scrape.example/only-resource.pdf",
                        }
                    ]
                ),
                tool_call_id="call-orphan",
            ),
        ]
    }

    result = await extract_citations_node(state)  # type: ignore[arg-type]
    assert result["citations"] == []
