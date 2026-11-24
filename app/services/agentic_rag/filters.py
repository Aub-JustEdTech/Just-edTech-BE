"""
Filter construction for the agentic RAG district-analytics tools.

Single source of truth for translating agent-facing parameters (topic
categories, subtopics, action_types, action_stages, meeting doc types,
meeting bodies, speaker names/roles, district names, states, time
windows, entity types) into the `must_match` / `must_match_any` /
`nested_match_any` / `nested_subtopic_match_any` / `range_match`
primitives that `QdrantStore._build_payload_filter` consumes.

The heatmap *engine* endpoint (`HeatmapEngineService`) uses a narrower
filter surface (categories + timeframe + district + state) and has its
own `_build_filter_fragments`. This module covers the richer agent
surface and is used by:

- `app/services/agentic_rag/tools.py::search_knowledge_base` (extended)
- `app/services/agentic_rag/tools.py::count_districts_by_topic`
- `app/services/agentic_rag/tools.py::get_district_citations`

Keeping the assembly in one place means a single test covers the whole
mapping and the agent tools don't have to rebuild Qdrant conditions
inline.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any, TypedDict

from app.schemas.heatmap_engine import TimeframePreset
from app.services.heatmap_engine.timeframe import (
    build_date_range_filter,
    build_timeframe_filter,
)

# ---------------------------------------------------------------------------
# Payload field names — kept here so the agent tools and any future
# introspection tooling share a single source of truth.
# ---------------------------------------------------------------------------

FIELD_DISTRICT_NAME = "district_name"
FIELD_STATE = "state"
FIELD_CLASSIFIED = "classified"
FIELD_TOPIC_TAGS = "topic_tags"  # array of {category, subtopic}
FIELD_TOPICS = "topics"  # coarse array (legacy, see taxonomy.TOPICS)
FIELD_ACTION_TYPES = "action_types"  # array
FIELD_ACTION_STAGE = "action_stage"  # single string
FIELD_MEETING_DOC_TYPE = "meeting_doc_type"  # single string
FIELD_MEETING_BODY = "meeting_body"  # single string
FIELD_ENTITY_TYPE = "entity_type"  # single string
FIELD_MEETING_DATE = "meeting_date"  # ISO date, DATETIME-indexed
FIELD_SCHOOL_YEAR = "school_year"  # e.g. "2025-2026"
FIELD_QUARTER_MONTH = "quarter_month"  # e.g. "2026-03"
FIELD_SPEAKERS = "speakers"  # array of {name, role}
FIELD_KEYWORD_FLAGS = "keyword_flags"  # lexical A4 safety-net array


# Classifier / prompt drift: the same concept is stored under several
# literal subtopic strings across the corpus. Expanding aliases here
# means the agent (and sample scripts) can pass any one spelling and
# still match chunks tagged with a sibling form.
_SUBTOPIC_ALIAS_GROUPS: tuple[frozenset[str], ...] = (
    frozenset(
        {
            "curriculum.chpe_framework",
            "curriculum_chpe_framework",
            "chpe_framework",
        }
    ),
    frozenset({"curriculum.3rs", "curriculum_3rs"}),
    frozenset({"curriculum.get_real", "curriculum_get_real"}),
    frozenset({"change.expansion", "change_expansion"}),
    frozenset({"change.reduction", "change_reduction"}),
    frozenset({"change.under_review", "change_under_review"}),
    # Taxonomy drift noted in golden_dataset_sample_answers.md
    frozenset({"gender_identity_discussion", "gender_identity"}),
)


def expand_topic_subtopics(subtopics: list[str] | None) -> list[str] | None:
    """Expand known alias spellings so one label matches corpus variants.

    Preserves caller order for the first occurrence of each alias group,
    then appends the remaining aliases. Returns ``None`` when input is
    empty/None so callers can treat "no subtopic filter" uniformly.
    """
    if not subtopics:
        return None
    expanded: list[str] = []
    seen: set[str] = set()
    for raw in subtopics:
        label = (raw or "").strip()
        if not label or label in seen:
            continue
        group = next((g for g in _SUBTOPIC_ALIAS_GROUPS if label in g), None)
        members = group if group is not None else frozenset({label})
        # Prefer the caller's spelling first, then the rest sorted for
        # stable Qdrant filters across runs.
        ordered = [label, *sorted(m for m in members if m != label)]
        for member in ordered:
            if member not in seen:
                seen.add(member)
                expanded.append(member)
    return expanded or None


class FilterFragments(TypedDict, total=False):
    """Typed alias for the dict returned by `build_filter_fragments`.

    Keys mirror the kwargs of `QdrantStore._build_payload_filter`:
    `must_match`, `must_match_any`, `nested_match_any`,
    `nested_subtopic_match_any`, `nested_field_match_any`, `range_match`.
    Passed as `**fragments`.
    """

    must_match: dict[str, Any]
    must_match_any: dict[str, list]
    nested_match_any: dict[str, list]
    nested_subtopic_match_any: dict[str, list]
    nested_field_match_any: dict[str, dict[str, list]]
    range_match: dict[str, dict[str, str]]


def _coerce_iso_date(value: str | date | datetime | None) -> str | None:
    """Accept date / datetime / ISO string and return `YYYY-MM-DD`."""
    if value is None:
        return None
    if isinstance(value, (datetime,)):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    text = str(value).strip()
    if not text:
        return None
    # Trim a trailing time component if the caller passed a full ISO
    # datetime; the Qdrant `meeting_date` payload is day-granular.
    if "T" in text:
        text = text.split("T", 1)[0]
    return text


def parse_time_window(
    *,
    timeframe: TimeframePreset | str | None,
    meeting_date_from: str | date | datetime | None,
    meeting_date_to: str | date | datetime | None,
    today: date | None = None,
) -> tuple[TimeframePreset | None, str | None, str | None]:
    """Resolve the agent's time-window inputs.

    Returns `(timeframe_preset_or_None, iso_from, iso_to)`. An explicit
    date range wins over a preset when both are supplied — mirrors the
    engine endpoint's behaviour. Relative phrases ("last 12 months",
    "since sept 2025") are not parsed here; the agent's prompt teaches
    it to translate them into `meeting_date_from`/`meeting_date_to`
    before calling the tool, so by the time we get here they are
    concrete ISO dates.

    `today` is injectable for tests; defaults to `date.today()`.
    """
    today = today or date.today()

    preset: TimeframePreset | None = None
    if timeframe is not None and not isinstance(timeframe, TimeframePreset):
        try:
            preset = TimeframePreset(str(timeframe))
        except ValueError:
            preset = None
    else:
        preset = timeframe

    iso_from = _coerce_iso_date(meeting_date_from)
    iso_to = _coerce_iso_date(meeting_date_to)

    # An explicit range wins over a preset.
    if iso_from and iso_to:
        return None, iso_from, iso_to
    if iso_from and not iso_to:
        # Open-ended "since X" → today as the upper bound.
        return None, iso_from, today.isoformat()
    if iso_to and not iso_from:
        # Open-ended upper bound: leave the lower bound empty and let
        # the preset apply if any; otherwise drop the range entirely.
        if preset is None:
            return None, None, None
        return preset, None, iso_to
    if preset is not None:
        return preset, None, None
    return None, None, None


def build_filter_fragments(
    *,
    topics: list[str] | None = None,
    topic_categories: list[str] | None = None,
    topic_subtopics: list[str] | None = None,
    action_types: list[str] | None = None,
    action_stages: list[str] | None = None,
    meeting_doc_types: list[str] | None = None,
    meeting_bodies: list[str] | None = None,
    entity_types: list[str] | None = None,
    districts: list[str] | None = None,
    states: list[str] | None = None,
    speaker_names: list[str] | None = None,
    speaker_roles: list[str] | None = None,
    school_years: list[str] | None = None,
    quarter_months: list[str] | None = None,
    keyword_flags: list[str] | None = None,
    timeframe: TimeframePreset | str | None = None,
    meeting_date_from: str | date | datetime | None = None,
    meeting_date_to: str | date | datetime | None = None,
    require_classified: bool = True,
    today: date | None = None,
) -> FilterFragments:
    """Translate agent-facing filter parameters into Qdrant primitives.

    Returns a dict suitable for `**fragments`-splatting into
    `QdrantStore.search(filters=...)`, `.count_chunks(...)` or
    `.filter_chunks(...)`.

    Semantics:

    - `topics` (coarse array, e.g. `["sex_education"]`) →
      `must_match_any` on the `topics` payload field.
    - `topic_categories` (fine, e.g. `["sexed"]`) →
      `nested_match_any` on `topic_tags.category`.
    - `topic_subtopics` (fine, e.g. `["comprehensive"]`) →
      `nested_subtopic_match_any` on `topic_tags.subtopic`.
    - `action_types` (array) → `must_match_any`.
    - `action_stages` / `meeting_doc_types` / `meeting_bodies` /
      `entity_types` (single-string payload fields) → `must_match_any`.
    - `districts` → `must_match_any` on `district_name`.
    - `states` → `must_match_any` on `state`.
    - `speaker_names` / `speaker_roles` → `nested_field_match_any`
      on `speakers.name` / `speakers.role` (nested-object array —
      `speakers` is `[{name, role}]`). Both can be active at once and
      are ANDed within the same NestedCondition so the match must
      occur inside the same speaker object.
    - `keyword_flags` (lexical A4 array, e.g. `["CHPE Framework"]`) →
      `must_match_any` on `keyword_flags`. Use as a classifier safety
      net when taxonomy tags are sparse or drifted.
    - `school_years` / `quarter_months` → `must_match_any`.
    - `timeframe` (preset) or `meeting_date_from` / `meeting_date_to`
      (explicit ISO range) → `must_match_any` on `school_year` /
      `quarter_month` (preset) or `range_match` on `meeting_date`
      (explicit). The explicit range wins when both are supplied.
    - `require_classified=True` → `must_match={"classified": True}` so
      un-classified chunks (e.g. freshly ingested but not yet
      batch-classified) are excluded by default.
    """
    must_match: dict[str, Any] = {}
    must_match_any: dict[str, list] = {}
    nested_match_any: dict[str, list] = {}
    nested_subtopic_match_any: dict[str, list] = {}
    nested_field_match_any: dict[str, dict[str, list]] = {}
    range_match: dict[str, dict[str, str]] = {}

    if require_classified:
        must_match[FIELD_CLASSIFIED] = True

    if topics:
        must_match_any[FIELD_TOPICS] = list(topics)
    if topic_categories:
        nested_match_any[FIELD_TOPIC_TAGS] = list(topic_categories)
    expanded_subtopics = expand_topic_subtopics(topic_subtopics)
    if expanded_subtopics:
        nested_subtopic_match_any[FIELD_TOPIC_TAGS] = expanded_subtopics
    if action_types:
        must_match_any[FIELD_ACTION_TYPES] = list(action_types)
    if action_stages:
        must_match_any[FIELD_ACTION_STAGE] = list(action_stages)
    if meeting_doc_types:
        must_match_any[FIELD_MEETING_DOC_TYPE] = list(meeting_doc_types)
    if meeting_bodies:
        must_match_any[FIELD_MEETING_BODY] = list(meeting_bodies)
    if entity_types:
        must_match_any[FIELD_ENTITY_TYPE] = list(entity_types)
    if keyword_flags:
        must_match_any[FIELD_KEYWORD_FLAGS] = list(keyword_flags)
    if districts:
        # Single district → use the cheaper `must_match` equality.
        if len(districts) == 1:
            must_match[FIELD_DISTRICT_NAME] = districts[0]
        else:
            must_match_any[FIELD_DISTRICT_NAME] = list(districts)
    if states:
        if len(states) == 1:
            must_match[FIELD_STATE] = states[0]
        else:
            must_match_any[FIELD_STATE] = list(states)
    if speaker_names:
        nested_field_match_any.setdefault(FIELD_SPEAKERS, {})[
            "name"
        ] = list(speaker_names)
    if speaker_roles:
        nested_field_match_any.setdefault(FIELD_SPEAKERS, {})[
            "role"
        ] = list(speaker_roles)
    if school_years:
        must_match_any[FIELD_SCHOOL_YEAR] = list(school_years)
    if quarter_months:
        must_match_any[FIELD_QUARTER_MONTH] = list(quarter_months)

    # Time window — explicit range wins over preset.
    preset, iso_from, iso_to = parse_time_window(
        timeframe=timeframe,
        meeting_date_from=meeting_date_from,
        meeting_date_to=meeting_date_to,
        today=today,
    )
    if iso_from and iso_to:
        start = date.fromisoformat(iso_from)
        end = date.fromisoformat(iso_to)
        range_match.update(build_date_range_filter(start, end))
    elif preset is not None:
        bucket = build_timeframe_filter(preset)
        if bucket:
            # `build_timeframe_filter` returns a single-key dict whose
            # key is `school_year` or `quarter_month`; merge into our
            # `must_match_any` so it composes with other filters.
            must_match_any.update(bucket)

    fragments: dict[str, Any] = {}
    if must_match:
        fragments["must_match"] = must_match
    if must_match_any:
        fragments["must_match_any"] = must_match_any
    if nested_match_any:
        fragments["nested_match_any"] = nested_match_any
    if nested_subtopic_match_any:
        fragments["nested_subtopic_match_any"] = nested_subtopic_match_any
    if nested_field_match_any:
        fragments["nested_field_match_any"] = nested_field_match_any
    if range_match:
        fragments["range_match"] = range_match
    return fragments  # type: ignore[return-value]


def relative_window(months: int, *, today: date | None = None) -> tuple[str, str]:
    """Return `(iso_from, iso_to)` covering the last `months` months.

    Convenience used by the agent's prompt examples; the agent itself
    passes concrete ISO dates, but tests and a future prompt-rewrite
    shortcut can call this to materialise a rolling window.
    """
    today = today or date.today()
    start = today - timedelta(days=int(months) * 30)
    if start > today:
        start, today = today, start
    return start.isoformat(), today.isoformat()
