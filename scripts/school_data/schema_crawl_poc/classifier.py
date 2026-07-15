"""
LLM page classifier — the heart of the schema-driven crawler.

One LLM call per crawled page. The model receives the page rendered as
markdown-with-links and returns a RelevantPage structured object. All
decision logic (ranking, archival skipping, frontier pruning) lives in
the crawler, NOT in the LLM — small models are bad at tool use but good
at structured extraction.

Reuses app.services.llm.client (OpenAI/OpenRouter) and follows the same
pattern as app/services/heatmap_ingest/doc_classifier.py.
"""

from __future__ import annotations

import json
import logging
from datetime import date
from typing import Any

from pydantic import ValidationError

from app.core.config import settings
from app.services.llm.client import (
    get_async_openai_client,
    get_llm_api_key,
    normalize_model_name,
)
from scripts.school_data.schema_crawl_poc.schemas import (
    DATA_TYPES,
    RelevantPage,
)

logger = logging.getLogger(__name__)


_SYSTEM_PROMPT = """You classify pages on K-12 US school district websites.

Given a page rendered as markdown (with visible link text and URLs), decide:
1. Does this page DIRECTLY host school board / district policy documents or media (PDFs of agendas, minutes, policies; audio/video of meetings)? -> has_data
2. Does this page link to subpages that host such documents? -> has_data_links
3. If has_data is true, what kind of board material is it, is it an archive of a past school year, and which calendar years are present?
4. Which same-domain links on this page are likely to lead to relevant documents? Assign each a confidence in [0.0, 1.0].

School board documents of interest: agendas, minutes, packets, policies, resolutions, public comments, book challenges, candidate profiles, election records, news coverage, advocacy material.

Do NOT mark routine navigation, calendars, staff directories, lunch menus, sports pages, or general school news as has_data unless they directly host board/policy material.

Return ONLY the JSON object. No prose, no markdown fences."""


def _response_format_schema() -> dict[str, Any]:
    """Build the JSON-schema enforced via response_format=json_schema."""
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "RelevantPage",
            "strict": True,
            "schema": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "url": {"type": "string"},
                    "title": {"type": "string"},
                    "has_data": {"type": "boolean"},
                    "has_data_links": {"type": "boolean"},
                    "description": {
                        "type": ["string", "null"],
                    },
                    "data_page_info": {
                        "type": ["object", "null"],
                        "properties": {
                            "data_type": {
                                "type": "string",
                                "enum": list(DATA_TYPES),
                            },
                            "is_archive": {"type": "boolean"},
                            "data_years_available": {
                                "type": "array",
                                "items": {"type": "integer"},
                            },
                            "confidence": {"type": "number"},
                        },
                        "required": [
                            "data_type",
                            "is_archive",
                            "data_years_available",
                            "confidence",
                        ],
                        "additionalProperties": False,
                    },
                    "possible_relevant_pages": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "url": {"type": "string"},
                                "confidence": {"type": "number"},
                                "reason": {
                                    "type": ["string", "null"],
                                },
                            },
                            "required": ["url", "confidence", "reason"],
                            "additionalProperties": False,
                        },
                    },
                },
                "required": [
                    "url",
                    "title",
                    "has_data",
                    "has_data_links",
                    "description",
                    "data_page_info",
                    "possible_relevant_pages",
                ],
            },
        },
    }


class PageClassifier:
    """Classify a single page into a RelevantPage via a cheap LLM call."""

    def __init__(
        self,
        model: str | None = None,
        timeout_s: float = 60.0,
        max_completion_tokens: int = 1000,
    ):
        # Reuse the heatmap doc-classifier model by default — it's already
        # configured for cheap structured extraction via OpenRouter.
        self._model = normalize_model_name(
            model
            or getattr(
                settings,
                "SCHOOL_SCRAPER_LLM_PAGE_CLASSIFIER_MODEL",
                settings.HEATMAP_INGEST_DOC_CLASSIFIER_MODEL,
            )
        )
        get_llm_api_key()
        self._client = get_async_openai_client(timeout=timeout_s)
        self._max_completion_tokens = max_completion_tokens

    async def classify(
        self,
        url: str,
        markdown: str,
        today: date | None = None,
    ) -> RelevantPage:
        """Classify one page. Returns a RelevantPage; never raises (falls back to a no-data page on error)."""
        today = today or date.today()
        # Trim to a token-ish budget. ~4 chars/token, cap at ~12k chars to keep
        # the per-page call well under $0.001 on gpt-4o-mini.
        trimmed = markdown[:12000]
        user_msg = self._build_user_message(url, trimmed, today)

        try:
            response = await self._client.chat.completions.create(
                model=self._model,
                messages=[
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {"role": "user", "content": user_msg},
                ],
                temperature=0,
                max_completion_tokens=self._max_completion_tokens,
                response_format=_response_format_schema(),
            )
            raw = response.choices[0].message.content or "{}"
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            logger.warning(
                "Page classifier returned non-JSON for %s: %s. Raw: %.200r",
                url,
                exc,
                raw,
            )
            payload = _fallback_payload(url)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Page classifier LLM call failed for %s: %s",
                url,
                exc,
                exc_info=True,
            )
            payload = _fallback_payload(url)

        # Force the URL to the actual crawled URL — the model sometimes echoes
        # a slightly different normalization (trailing slash, fragment, etc.).
        payload["url"] = url

        try:
            return RelevantPage.model_validate(payload)
        except ValidationError as exc:
            logger.warning(
                "Page classifier schema violation for %s: %s. Payload: %.500r",
                url,
                exc,
                payload,
            )
            return RelevantPage.model_validate(_fallback_payload(url))

    @staticmethod
    def _build_user_message(url: str, markdown: str, today: date) -> str:
        return (
            f"URL: {url}\n"
            f"CURRENT_DATE: {today.isoformat()} (school year is "
            f"{today.year if today.month >= 8 else today.year - 1}-"
            f"{(today.year + 1) if today.month >= 8 else today.year})\n\n"
            f"PAGE MARKDOWN:\n{markdown}"
        )


def _fallback_payload(url: str) -> dict[str, Any]:
    return {
        "url": url,
        "title": "",
        "has_data": False,
        "has_data_links": False,
        "description": None,
        "data_page_info": None,
        "possible_relevant_pages": [],
    }
