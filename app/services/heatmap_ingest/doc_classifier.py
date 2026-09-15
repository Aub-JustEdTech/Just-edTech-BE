"""
Doc-level classifier for the heatmap ingest pipeline.

Runs once per document (sync, gpt-4o-mini) to extract:
  - entity_type  (one of ENTITY_TYPES, single-label)
  - doc_kind     (agenda | minutes | packet | resolution | policy | news | other)
  - meeting_date (ISO YYYY-MM-DD if extractable, else None)

Called by step2_6_classify_document in app/tasks/document_pipeline.py,
gated on Document.source_type == 'school_scraper' so non-heatmap tenants
are unaffected.

The classifier is intentionally cheap (one call per ~30k docs ≈ $3 total)
because it only needs to read the filename + first page of text. A regex
fallback handles the most common meeting-date patterns before the LLM is
called, so well-formed filenames skip the LLM cost entirely for the date
field (the LLM still classifies entity_type / doc_kind).
"""

import json
import logging
import re
from typing import Any

from langsmith import traceable
from pydantic import ValidationError

from app.core.config import settings
from app.services.heatmap_ingest.taxonomy import (
    ENTITY_TYPES,
    MEETING_BODIES,
    MEETING_DOC_TYPES,
    DocClassification,
)
from app.services.llm.client import (
    get_async_openai_client,
    get_llm_api_key,
    normalize_model_name,
)

logger = logging.getLogger(__name__)


# ── Meeting-date regex fallback ────────────────────────────────────────────────

# Matches YYYY-MM-DD, YYYY/MM/DD, MM/DD/YYYY, MM-DD-YYYY, "Month DD, YYYY", "DD Month YYYY".
# Boundary: any char that is NOT a letter or digit. We deliberately allow
# `_`, `.`, `-`, `/` as boundary chars so the pattern fires inside
# filenames like 'agenda_03-14-2025.pdf'. (Using '\b' would not fire
# between '_' and a digit because '_' is a word char.)
_DATE_BOUNDARY = r"(?<![A-Za-z0-9])"
_DATE_PATTERNS: tuple[re.Pattern[str], ...] = (
    # 2025-03-14 or 2025/03/14 (year first, 4 digits)
    re.compile(_DATE_BOUNDARY + r"(?P<y>\d{4})[-/](?P<m>\d{1,2})[-/](?P<d>\d{1,2})"),
    # 03/14/2025 or 03-14-2025 or 3/14/2025 (month first, year last 4 digits)
    re.compile(_DATE_BOUNDARY + r"(?P<m>\d{1,2})[-/](?P<d>\d{1,2})[-/](?P<y>\d{4})"),
    # March 14, 2025  /  Mar 14 2025
    re.compile(
        _DATE_BOUNDARY + r"(?P<mon>January|February|March|April|May|June|July|August|"
        r"September|October|November|December|Jan|Feb|Mar|Apr|May|Jun|"
        r"Jul|Aug|Sep|Sept|Oct|Nov|Dec)\s+"
        r"(?P<d>\d{1,2})(?:st|nd|rd|th)?,?\s+(?P<y>\d{4})",
        re.IGNORECASE,
    ),
    # 14 March 2025
    re.compile(
        _DATE_BOUNDARY + r"(?P<d>\d{1,2})(?:st|nd|rd|th)?\s+"
        r"(?P<mon>January|February|March|April|May|June|July|August|"
        r"September|October|November|December|Jan|Feb|Mar|Apr|May|Jun|"
        r"Jul|Aug|Sep|Sept|Oct|Nov|Dec),?\s+(?P<y>\d{4})",
        re.IGNORECASE,
    ),
)

_MONTH_TO_NUM: dict[str, str] = {
    "jan": "01", "feb": "02", "mar": "03", "apr": "04", "may": "05", "jun": "06",
    "jul": "07", "aug": "08", "sep": "09", "sept": "09", "oct": "10",
    "nov": "11", "dec": "12",
    "january": "01", "february": "02", "march": "03", "april": "04",
    "june": "06", "july": "07", "august": "08", "september": "09",
    "october": "10", "november": "11", "december": "12",
}


def _extract_meeting_date_regex(text: str) -> str | None:
    """Try to pull an ISO YYYY-MM-DD date out of a filename / first-page blob."""
    for pat in _DATE_PATTERNS:
        m = pat.search(text)
        if not m:
            continue
        gd = m.groupdict()
        try:
            if "mon" in gd:
                mon_str = _MONTH_TO_NUM.get(gd["mon"].lower())
                if not mon_str:
                    continue
                mon = int(mon_str)
                year, day = int(gd["y"]), int(gd["d"])
            else:
                mon = int(gd["m"])
                year, day = int(gd["y"]), int(gd["d"])
            # Sanity-check it's a real date.
            from datetime import date as _date

            iso = _date(year, mon, day).isoformat()
            return iso
        except (ValueError, KeyError):
            continue
    return None


# ── LLM call ───────────────────────────────────────────────────────────────────

_DOC_SYSTEM_PROMPT = """You classify K-12 US school district documents at the DOCUMENT level (one label per document, not per chunk).

Given a document's filename and the first ~6000 characters of extracted text, return a JSON object with:

- entity_type: ONE of
  board_minutes, board_agenda, policy_document, book_challenge,
  public_comment, candidate_profile, election_record, news_media, advocacy_intervention

- doc_kind: ONE of
  agenda, minutes, packet, resolution, policy, news, other

- meeting_date: ISO date string (YYYY-MM-DD) if a meeting date is unambiguously stated or implied by the filename/text, else null.

- meeting_doc_type: ONE of
  "Minutes", "Agenda", "Agenda Attachment", "Public Comment Transcript",
  "Policy Document", "Presentation Slide"
  or null if it cannot be determined.

- meeting_body: ONE of
  "Full Board", "Curriculum Subcommittee", "Policy Subcommittee",
  "Public Hearing", "Special Meeting"
  or null if it cannot be determined.

Return ONLY the JSON object. No prose, no markdown fences."""


def _doc_response_format_schema() -> dict[str, Any]:
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "DocClassification",
            "strict": True,
            "schema": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "entity_type": {"type": "string", "enum": list(ENTITY_TYPES)},
                    "doc_kind": {
                        "type": "string",
                        "enum": [
                            "agenda", "minutes", "packet",
                            "resolution", "policy", "news", "other",
                        ],
                    },
                    "meeting_date": {
                        "type": ["string", "null"],
                    },
                    "meeting_doc_type": {
                        "type": ["string", "null"],
                        "enum": [None, *list(MEETING_DOC_TYPES)],
                    },
                    "meeting_body": {
                        "type": ["string", "null"],
                        "enum": [None, *list(MEETING_BODIES)],
                    },
                },
                "required": [
                    "entity_type",
                    "doc_kind",
                    "meeting_date",
                    "meeting_doc_type",
                    "meeting_body",
                ],
            },
        },
    }


class DocClassifier:
    """
    Classifies a single document's entity_type, doc_kind, and meeting_date.

    One LLM call per document. Cheap (gpt-4o-mini, ~$3 for 30k docs).
    """

    def __init__(self, model: str | None = None, timeout_s: float = 60.0):
        self._model = normalize_model_name(
            model or getattr(
                settings, "HEATMAP_INGEST_DOC_CLASSIFIER_MODEL", "openai/gpt-4o-mini"
            )
        )
        get_llm_api_key()
        self._client = get_async_openai_client(timeout=timeout_s)

    @traceable(name="doc_classifier_classify")
    async def classify(
        self,
        filename: str,
        first_page_text: str,
        source_metadata: dict[str, Any] | None = None,
    ) -> DocClassification:
        """
        Classify one document. Tries regex date extraction first; falls back
        to the LLM for the date if regex finds nothing.
        """
        # 1. Regex date fallback (filename often contains the date).
        regex_date: str | None = None
        try:
            regex_date = _extract_meeting_date_regex(filename) or (
                _extract_meeting_date_regex(first_page_text[:2000])
                if first_page_text
                else None
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug(f"Regex date extraction failed: {exc}")

        # 2. LLM call for entity_type + doc_kind (+ meeting_date if regex missed).
        user_msg = self._build_user_message(
            filename, first_page_text, source_metadata, regex_date
        )
        try:
            response = await self._client.chat.completions.create(
                model=self._model,
                messages=[
                    {"role": "system", "content": _DOC_SYSTEM_PROMPT},
                    {"role": "user", "content": user_msg},
                ],
                temperature=0,
                max_completion_tokens=200,
                response_format=_doc_response_format_schema(),
            )
            raw = response.choices[0].message.content or "{}"
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            logger.error(
                f"Doc classifier returned non-JSON for {filename!r}: {exc}. Raw: {raw!r}"
            )
            # Fall back to a permissive default so the pipeline can continue.
            payload = {
                "entity_type": "board_minutes",
                "doc_kind": "other",
                "meeting_date": regex_date,
                "meeting_doc_type": None,
                "meeting_body": None,
            }
        except Exception as exc:
            logger.error(f"Doc classifier LLM call failed for {filename!r}: {exc}", exc_info=True)
            payload = {
                "entity_type": "board_minutes",
                "doc_kind": "other",
                "meeting_date": regex_date,
                "meeting_doc_type": None,
                "meeting_body": None,
            }

        # 3. Prefer the regex date over the LLM date when both are present,
        #    because regex is more reliable on well-formed filenames.
        if regex_date:
            payload["meeting_date"] = regex_date

        try:
            return DocClassification.model_validate(payload)
        except ValidationError as exc:
            logger.error(
                f"Doc classifier schema violation for {filename!r}: {exc}. Payload: {payload!r}"
            )
            # Last-resort fallback so a single bad doc doesn't fail the pipeline.
            return DocClassification(
                entity_type="board_minutes",
                doc_kind="other",
                meeting_date=regex_date,
                meeting_doc_type=None,
                meeting_body=None,
            )

    @staticmethod
    def _build_user_message(
        filename: str,
        first_page_text: str,
        source_metadata: dict[str, Any] | None,
        regex_date: str | None,
    ) -> str:
        parts: list[str] = []
        parts.append(f"FILENAME: {filename}")
        if source_metadata:
            # Pass through a small subset — keep prompt token budget tight.
            for key in ("school_name", "district_type", "meeting_date", "media_type"):
                if key in source_metadata and source_metadata[key]:
                    parts.append(f"{key.upper()}: {source_metadata[key]}")
        if regex_date:
            parts.append(f"REGEX_DETECTED_DATE: {regex_date}")
        parts.append("")
        parts.append("FIRST PAGE TEXT:")
        # Cap at 6000 chars to keep the per-doc call under ~2k tokens.
        parts.append((first_page_text or "")[:6000])
        return "\n".join(parts)
