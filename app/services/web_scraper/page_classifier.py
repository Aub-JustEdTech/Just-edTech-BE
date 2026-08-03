"""
LLM page classifier — the heart of the schema-driven crawler.

One LLM call per crawled page. The model receives the page rendered as
markdown-with-links and returns a RelevantPage structured object. All
decision logic (ranking, archival skipping, frontier pruning) lives in the
crawler (schema_driven_crawler.py), NOT in the LLM — small models are bad at
tool use but good at structured extraction.

Reuses app.services.llm.client (OpenAI/OpenRouter) and follows the same
pattern as app/services/heatmap_ingest/doc_classifier.py.

Promoted from scripts/school_data/schema_crawl_poc/classifier.py. The POC
scripts now import from here so the two cannot drift.
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
from app.services.web_scraper.page_schemas import DATA_TYPES, RelevantPage

logger = logging.getLogger(__name__)


_SYSTEM_PROMPT = """You classify pages on K-12 US school district websites.

Your ONLY goal is to find meeting MINUTES and meeting AGENDAS for school board meetings.

Given a page rendered as markdown (with visible link text and URLs), decide:
1. Does this page DIRECTLY host meeting minutes or meeting agendas (PDFs, embedded documents, audio/video of board meetings)? -> has_data
2. Does this page link to subpages that host meeting minutes or agendas? -> has_data_links
3. If has_data is true, is it minutes or agendas, is it an archive of a past school year, and which calendar years are present?
4. Which same-domain links on this page are likely to lead to pages with meeting minutes or agendas? Assign each a confidence in [0.0, 1.0].

IGNORE pages about: policies, book challenges, public comments, candidate profiles, election records, news, advocacy, budgets, staff directories, calendars, lunch menus, sports.

Return ONLY the JSON object. No prose, no markdown fences."""


def _response_format_schema() -> dict[str, Any]:
    """Build the JSON-schema enforced via response_format=json_schema.

    Derived from RelevantPage.model_json_schema() so the schema cannot drift
    from the Pydantic model. OpenAI structured outputs require a few
    post-processing tweaks: strip $defs/$ref by inlining is avoided here
    because the model nests PossibleRelevantPage and DataPageInfo; instead we
    rely on Pydantic's $defs and rewrite $ref to inline definitions, then
    enforce additionalProperties=False and required at every level (strict mode).
    """
    schema = RelevantPage.model_json_schema()
    defs = schema.pop("$defs", {})

    def _inline(node: Any) -> Any:
        if isinstance(node, dict):
            if "$ref" in node:
                ref_name = node["$ref"].split("/")[-1]
                return _inline({k: v for k, v in defs.get(ref_name, {}).items() if k != "title"})
            return {k: _inline(v) for k, v in node.items()}
        if isinstance(node, list):
            return [_inline(x) for x in node]
        return node

    inlined = _inline(schema)
    # Enforce strict-mode requirements: every property must be listed as required
    # and additionalProperties must be False at every object level.
    def _strictify(node: Any) -> Any:
        if isinstance(node, dict):
            if node.get("type") == "object" and "properties" in node:
                node["required"] = list(node["properties"].keys())
                node["additionalProperties"] = False
            for v in node.values():
                _strictify(v)
        elif isinstance(node, list):
            for x in node:
                _strictify(x)
        return node

    _strictify(inlined)
    # data_type enum is preserved by Pydantic's json_schema for Literal/Enum, but
    # our data_type is a plain str with a description — ensure the enum is set
    # explicitly so the model cannot return out-of-vocabulary types.
    _enforce_data_type_enum(inlined)
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "RelevantPage",
            "strict": True,
            "schema": inlined,
        },
    }


def _enforce_data_type_enum(node: Any) -> None:
    """Walk the schema and attach the DATA_TYPES enum to the data_type field."""
    if isinstance(node, dict):
        if node.get("type") == "object" and "properties" in node:
            props = node["properties"]
            if "data_type" in props and isinstance(props["data_type"], dict):
                props["data_type"]["enum"] = list(DATA_TYPES)
        for v in node.values():
            _enforce_data_type_enum(v)
    elif isinstance(node, list):
        for x in node:
            _enforce_data_type_enum(x)


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
        # Last LLM response metadata (finish_reason + token usage). Set after
        # each successful create(); None on error/fallback. Read by the eval
        # harness to measure truncation rate and cost. Non-breaking for the
        # crawler (it ignores this attribute).
        self.last_response_meta: dict[str, Any] | None = None

    async def classify(
        self,
        url: str,
        markdown: str,
        today: date | None = None,
    ) -> RelevantPage:
        """Classify one page. Returns a RelevantPage; never raises (falls back to a no-data page on error)."""
        self.last_response_meta = None
        today = today or date.today()
        # Trim to a token-ish budget. ~4 chars/token, cap at ~16k chars to keep
        # the per-page call well under $0.001 on gpt-4o-mini while preserving
        # long archive year-listings (which were getting truncated mid-list,
        # hurting is_archive / data_years_available recall).
        trimmed = markdown[:16000]
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
            self.last_response_meta = {
                "finish_reason": response.choices[0].finish_reason,
                "prompt_tokens": getattr(response.usage, "prompt_tokens", 0) or 0,
                "completion_tokens": getattr(response.usage, "completion_tokens", 0) or 0,
            }
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
