"""
Unit tests for PageClassifier — the LLM page classifier.

The OpenAI client is mocked; no real LLM calls are made. These tests cover:
  - happy path: valid JSON -> RelevantPage with data_page_info
  - board_agenda classification
  - JSON decode error -> fallback no-data RelevantPage
  - LLM call exception -> fallback no-data RelevantPage
  - schema validation error -> fallback no-data RelevantPage
  - last_response_meta is populated on success and None on failure
  - data_type enum constraint is enforced and limited to minutes/agendas
  - possible_relevant_pages confidence handling

Run:
    poetry run pytest tests/test_page_classifier.py -v
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio  # noqa: F401

from app.services.web_scraper.page_classifier import (
    PageClassifier,
    _response_format_schema,
)
from app.services.web_scraper.page_schemas import DATA_TYPES, RelevantPage

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_response(content: str, *, finish_reason: str = "stop", usage=None):
    """Build a fake OpenAI ChatCompletion response object."""
    choice = MagicMock()
    choice.message.content = content
    choice.finish_reason = finish_reason
    resp = MagicMock()
    resp.choices = [choice]
    resp.usage = usage or MagicMock(prompt_tokens=100, completion_tokens=50)
    return resp


def _minutes_payload(url: str = "https://example.com/minutes") -> str:
    return json.dumps(
        {
            "url": url,
            "title": "Board Meeting Minutes",
            "has_data": True,
            "has_data_links": False,
            "description": "Minutes from the January 2025 board meeting.",
            "data_page_info": {
                "data_type": "board_minutes",
                "is_archive": False,
                "data_years_available": [2025],
                "confidence": 0.95,
            },
            "possible_relevant_pages": [],
        }
    )


def _agenda_payload(url: str = "https://example.com/agenda") -> str:
    return json.dumps(
        {
            "url": url,
            "title": "Board Meeting Agenda",
            "has_data": True,
            "has_data_links": False,
            "description": "Agenda for the February 2025 board meeting.",
            "data_page_info": {
                "data_type": "board_agenda",
                "is_archive": False,
                "data_years_available": [2025],
                "confidence": 0.92,
            },
            "possible_relevant_pages": [],
        }
    )


def _no_data_payload(url: str = "https://example.com/staff") -> str:
    return json.dumps(
        {
            "url": url,
            "title": "Staff Directory",
            "has_data": False,
            "has_data_links": False,
            "description": "List of school staff members.",
            "data_page_info": None,
            "possible_relevant_pages": [],
        }
    )


def _with_links_payload(url: str = "https://example.com/board") -> str:
    return json.dumps(
        {
            "url": url,
            "title": "School Board",
            "has_data": False,
            "has_data_links": True,
            "description": "Board landing page with links to minutes and agendas.",
            "data_page_info": None,
            "possible_relevant_pages": [
                {
                    "url": "https://example.com/board/minutes",
                    "confidence": 0.9,
                    "reason": "link text says 'Meeting Minutes'",
                },
                {
                    "url": "https://example.com/board/agendas",
                    "confidence": 0.85,
                    "reason": "link text says 'Agendas'",
                },
            ],
        }
    )


def _make_classifier() -> PageClassifier:
    with patch("app.services.web_scraper.page_classifier.get_llm_api_key"), \
         patch(
             "app.services.web_scraper.page_classifier.get_async_openai_client",
             return_value=MagicMock(),
         ):
        return PageClassifier()


# ---------------------------------------------------------------------------
# Happy path — board_minutes
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_classify_board_minutes():
    classifier = _make_classifier()
    classifier._client.chat.completions.create = AsyncMock(
        return_value=_mock_response(_minutes_payload())
    )
    result = await classifier.classify("https://example.com/minutes", "# Minutes\n")

    assert isinstance(result, RelevantPage)
    assert result.has_data is True
    assert result.data_page_info is not None
    assert result.data_page_info.data_type == "board_minutes"
    assert result.data_page_info.is_archive is False
    assert result.url == "https://example.com/minutes"


# ---------------------------------------------------------------------------
# Happy path — board_agenda
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_classify_board_agenda():
    classifier = _make_classifier()
    classifier._client.chat.completions.create = AsyncMock(
        return_value=_mock_response(_agenda_payload())
    )
    result = await classifier.classify("https://example.com/agenda", "# Agenda\n")

    assert isinstance(result, RelevantPage)
    assert result.has_data is True
    assert result.data_page_info is not None
    assert result.data_page_info.data_type == "board_agenda"


# ---------------------------------------------------------------------------
# Non-relevant page (staff directory, policy, etc.)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_classify_irrelevant_page_returns_no_data():
    classifier = _make_classifier()
    classifier._client.chat.completions.create = AsyncMock(
        return_value=_mock_response(_no_data_payload())
    )
    result = await classifier.classify("https://example.com/staff", "# Staff\n")

    assert result.has_data is False
    assert result.has_data_links is False
    assert result.data_page_info is None


# ---------------------------------------------------------------------------
# Page with links to minutes/agendas
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_classify_page_with_links_to_minutes():
    classifier = _make_classifier()
    classifier._client.chat.completions.create = AsyncMock(
        return_value=_mock_response(_with_links_payload())
    )
    result = await classifier.classify("https://example.com/board", "# Board\n")

    assert result.has_data is False
    assert result.has_data_links is True
    assert len(result.possible_relevant_pages) == 2
    assert result.possible_relevant_pages[0].confidence == 0.9


# ---------------------------------------------------------------------------
# Response metadata
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_last_response_meta_populated_on_success():
    classifier = _make_classifier()
    classifier._client.chat.completions.create = AsyncMock(
        return_value=_mock_response(_minutes_payload(), finish_reason="stop")
    )
    await classifier.classify("https://example.com/minutes", "# Minutes\n")
    meta = classifier.last_response_meta
    assert meta is not None
    assert meta["finish_reason"] == "stop"
    assert meta["prompt_tokens"] == 100
    assert meta["completion_tokens"] == 50


@pytest.mark.asyncio
async def test_truncated_response_records_length_finish_reason():
    classifier = _make_classifier()
    classifier._client.chat.completions.create = AsyncMock(
        return_value=_mock_response(_minutes_payload(), finish_reason="length")
    )
    result = await classifier.classify("https://example.com/minutes", "# Minutes\n")
    assert result.has_data is True
    assert classifier.last_response_meta["finish_reason"] == "length"


# ---------------------------------------------------------------------------
# Fallback paths
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_json_decode_error_falls_back_to_no_data():
    classifier = _make_classifier()
    classifier._client.chat.completions.create = AsyncMock(
        return_value=_mock_response("not valid json {{{")
    )
    result = await classifier.classify("https://example.com/x", "# x\n")

    assert result.has_data is False
    assert result.has_data_links is False
    assert result.data_page_info is None
    assert result.url == "https://example.com/x"
    assert classifier.last_response_meta is None


@pytest.mark.asyncio
async def test_llm_call_exception_falls_back_to_no_data():
    classifier = _make_classifier()
    classifier._client.chat.completions.create = AsyncMock(
        side_effect=RuntimeError("API timeout")
    )
    result = await classifier.classify("https://example.com/x", "# x\n")

    assert result.has_data is False
    assert result.url == "https://example.com/x"
    assert classifier.last_response_meta is None


@pytest.mark.asyncio
async def test_schema_validation_error_falls_back_to_no_data():
    classifier = _make_classifier()
    bad_payload = json.dumps(
        {
            "url": "https://example.com/x",
            "title": "x",
            "has_data": True,
            "has_data_links": False,
            "description": None,
            "data_page_info": {"data_type": "board_minutes"},
            "possible_relevant_pages": [],
        }
    )
    classifier._client.chat.completions.create = AsyncMock(
        return_value=_mock_response(bad_payload)
    )
    result = await classifier.classify("https://example.com/x", "# x\n")

    assert result.has_data is False
    assert result.url == "https://example.com/x"


# ---------------------------------------------------------------------------
# Response-format schema — DATA_TYPES narrowed to minutes & agendas
# ---------------------------------------------------------------------------


def test_data_types_only_contains_minutes_agendas_unknown():
    assert set(DATA_TYPES) == {"board_minutes", "board_agenda", "unknown"}


def test_response_format_schema_has_data_type_enum():
    schema = _response_format_schema()
    assert schema["type"] == "json_schema"
    inner = schema["json_schema"]["schema"]
    assert inner["additionalProperties"] is False

    def find_dt(node):
        if isinstance(node, dict):
            if "data_type" in node.get("properties", {}):
                return node["properties"]["data_type"]
            for v in node.values():
                r = find_dt(v)
                if r:
                    return r
        elif isinstance(node, list):
            for x in node:
                r = find_dt(x)
                if r:
                    return r
        return None

    dt = find_dt(inner)
    assert dt is not None
    assert "enum" in dt
    assert set(dt["enum"]) == {"board_minutes", "board_agenda", "unknown"}


def test_response_format_schema_excludes_old_data_types():
    schema = _response_format_schema()
    inner = schema["json_schema"]["schema"]
    schema_str = json.dumps(inner)
    for old_type in ("policy_document", "book_challenge", "public_comment",
                     "candidate_profile", "election_record", "news_media",
                     "advocacy_intervention"):
        assert old_type not in schema_str, f"{old_type} should not be in schema"
