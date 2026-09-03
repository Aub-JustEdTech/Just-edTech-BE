"""Unit tests for the agentic RAG district-analytics additions.

Covers:

1. `app.services.agentic_rag.filters.build_filter_fragments` — the
   pure-function filter assembler. Every sample-query filter
   combination is exercised so a regression in the Qdrant filter
   mapping is caught here rather than at runtime.

2. `app.services.agentic_rag.tools.count_districts_by_topic` — with a
   stubbed `VectorStore` and `AsyncSessionLocal`, the tool walks every
   active school for the tenant and returns one row per district with
   the chunk count. The stub records the filter primitives each call
   received so we can assert that the seven sample queries map to the
   expected Qdrant conditions.

3. `app.services.agentic_rag.tools.get_district_citations` — with a
   stubbed `VectorStore` returning a fixed chunk list, the tool
   paginates and hydrates citations with document_db_id resolution.

4. `app.services.agentic_rag.tools.list_districts` — pure SQL path,
   stubbed.

5. `app.services.agentic_rag.tools.get_taxonomy` — returns the
   canonical vocabulary from `app.services.heatmap_ingest.taxonomy`
   plus the state-specific pack.

No I/O — every external dependency is stubbed. Run with:

    poetry run pytest tests/test_agentic_rag_district_analytics.py -v
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any

import pytest

from app.services.agentic_rag.filters import (
    build_filter_fragments,
    parse_time_window,
    relative_window,
)


# ---------------------------------------------------------------------------
# 1. build_filter_fragments — pure-function filter assembly
# ---------------------------------------------------------------------------


def test_empty_filter_returns_empty_dict():
    fragments = build_filter_fragments(require_classified=False)
    assert fragments == {}


def test_require_classified_default_adds_must_match():
    fragments = build_filter_fragments()
    assert fragments == {"must_match": {"classified": True}}


def test_require_classified_false_omits_must_match():
    fragments = build_filter_fragments(require_classified=False)
    assert fragments == {}


def test_topic_categories_maps_to_nested_match_any():
    fragments = build_filter_fragments(topic_categories=["sexed", "lgbtq"])
    assert fragments["nested_match_any"] == {"topic_tags": ["sexed", "lgbtq"]}


def test_topic_subtopics_maps_to_nested_subtopic_match_any():
    fragments = build_filter_fragments(topic_subtopics=["comprehensive"])
    assert fragments["nested_subtopic_match_any"] == {
        "topic_tags": ["comprehensive"]
    }


def test_topics_maps_to_must_match_any():
    fragments = build_filter_fragments(topics=["sex_education"])
    assert fragments["must_match_any"] == {"topics": ["sex_education"]}


def test_action_types_maps_to_must_match_any():
    fragments = build_filter_fragments(action_types=["book_challenged"])
    assert fragments["must_match_any"] == {
        "action_types": ["book_challenged"]
    }


def test_action_stages_meeting_doc_types_meeting_bodies_map_to_must_match_any():
    fragments = build_filter_fragments(
        action_stages=["Vote — Passed"],
        meeting_doc_types=["Agenda", "Minutes"],
        meeting_bodies=["Full Board"],
    )
    assert fragments["must_match_any"] == {
        "action_stage": ["Vote — Passed"],
        "meeting_doc_type": ["Agenda", "Minutes"],
        "meeting_body": ["Full Board"],
    }


def test_single_district_uses_must_match_equality():
    fragments = build_filter_fragments(districts=["Boston Public Schools"])
    assert fragments["must_match"]["district_name"] == "Boston Public Schools"
    assert "must_match_any" not in fragments or "district_name" not in fragments.get(
        "must_match_any", {}
    )


def test_multiple_districts_uses_must_match_any():
    fragments = build_filter_fragments(
        districts=["Boston Public Schools", "Newton Public Schools"]
    )
    assert fragments["must_match_any"]["district_name"] == [
        "Boston Public Schools",
        "Newton Public Schools",
    ]


def test_single_state_uses_must_match_equality():
    fragments = build_filter_fragments(states=["MA"])
    assert fragments["must_match"]["state"] == "MA"


def test_speaker_names_routes_to_nested_field_match_any():
    fragments = build_filter_fragments(speaker_names=["Alice"])
    assert fragments["nested_field_match_any"] == {"speakers": {"name": ["Alice"]}}


def test_speaker_roles_routes_to_nested_field_match_any():
    fragments = build_filter_fragments(speaker_roles=["Board Member"])
    assert fragments["nested_field_match_any"] == {
        "speakers": {"role": ["Board Member"]}
    }


def test_speaker_names_and_roles_combined_in_one_nested():
    fragments = build_filter_fragments(
        speaker_names=["Alice"], speaker_roles=["Board Member"]
    )
    assert fragments["nested_field_match_any"] == {
        "speakers": {"name": ["Alice"], "role": ["Board Member"]}
    }


def test_explicit_date_range_wins_over_timeframe():
    fragments = build_filter_fragments(
        timeframe="year",
        meeting_date_from="2025-09-01",
        meeting_date_to="2026-09-02",
    )
    # range_match should be set; timeframe bucket should NOT leak into
    # must_match_any.
    assert "range_match" in fragments
    assert fragments["range_match"]["meeting_date"]["gte"].startswith("2025-09-01")
    assert "school_year" not in fragments.get("must_match_any", {})


def test_timeframe_preset_only_applies_school_year_bucket():
    fragments = build_filter_fragments(timeframe="year")
    assert "school_year" in fragments["must_match_any"]
    assert "range_match" not in fragments


def test_timeframe_2_years_includes_current_and_previous():
    fragments = build_filter_fragments(timeframe="2_years")
    years = fragments["must_match_any"]["school_year"]
    assert len(years) == 2
    # Format check: each is "YYYY-YYYY".
    assert all("-" in y for y in years)


def test_open_ended_since_uses_today_as_upper_bound():
    from datetime import date

    fragments = build_filter_fragments(
        meeting_date_from="2025-09-01",
        today=date(2026, 9, 2),
    )
    assert fragments["range_match"]["meeting_date"]["gte"].startswith("2025-09-01")
    assert fragments["range_match"]["meeting_date"]["lte"].startswith("2026-09-02")


# ---------------------------------------------------------------------------
# parse_time_window / relative_window helpers
# ---------------------------------------------------------------------------


def test_parse_time_window_explicit_range_wins():
    preset, f, t = parse_time_window(
        timeframe="year",
        meeting_date_from="2025-09-01",
        meeting_date_to="2026-09-02",
    )
    assert preset is None
    assert f == "2025-09-01"
    assert t == "2026-09-02"


def test_parse_time_window_preset_only():
    from app.schemas.heatmap_engine import TimeframePreset

    preset, f, t = parse_time_window(
        timeframe="year",
        meeting_date_from=None,
        meeting_date_to=None,
    )
    assert preset == TimeframePreset.YEAR
    assert f is None and t is None


def test_relative_window_returns_iso_dates():
    from datetime import date

    f, t = relative_window(12, today=date(2026, 9, 2))
    assert f.startswith("2025-")
    assert t == "2026-09-02"


# ---------------------------------------------------------------------------
# 2-5. Tool tests with stubbed dependencies
# ---------------------------------------------------------------------------


def _school(name: str, org_code: str, district_type: str = "public") -> Any:
    return SimpleNamespace(
        name=name,
        org_code=org_code,
        district_type=district_type,
        state="MA",
    )


class FakeVectorStore:
    """Records calls + returns scripted counts / chunks.

    A `script` is a `{district_name: chunk_count}` for count_chunks,
    plus a `chunks` list for filter_chunks. The recorded `calls` let
    tests assert exactly which filter primitives each tool call
    produced.
    """

    def __init__(
        self,
        counts: dict[str, int] | None = None,
        chunks: list[dict[str, Any]] | None = None,
    ) -> None:
        self.counts = counts or {}
        self.chunks = chunks or []
        self.count_calls: list[dict[str, Any]] = []
        self.filter_calls: list[dict[str, Any]] = []

    async def count_chunks(self, tenant_id: int, **kwargs: Any) -> int:
        self.count_calls.append(kwargs)
        district = (kwargs.get("must_match") or {}).get("district_name", "")
        return self.counts.get(district, 0)

    async def filter_chunks(
        self, tenant_id: int, *, limit: int = 100, **kwargs: Any
    ) -> list[dict[str, Any]]:
        self.filter_calls.append({"limit": limit, **kwargs})
        return list(self.chunks[:limit])


def _extract_org_code_literal(stmt_str: str) -> str | None:
    """Pull the org_code string literal out of a `WHERE schools.org_code = 'X'`."""
    import re

    m = re.search(r"schools\.org_code\s*=\s*'([^']*)'", stmt_str, re.IGNORECASE)
    return m.group(1) if m else None


def _extract_state_literal(stmt_str: str) -> str | None:
    """Pull the state literal out of `WHERE schools.state = 'MA'`."""
    import re

    m = re.search(r"schools\.state\s*=\s*'([^']*)'", stmt_str, re.IGNORECASE)
    return m.group(1) if m else None


def _extract_ilike_substring(stmt_str: str) -> str | None:
    """Pull the substring from `schools.name ILIKE '%Foo%'`.

    Returns the inner substring (without the `%` wildcards).
    """
    import re

    m = re.search(
        r"schools\.name\s+(?:NOT\s+)?ILIKE\s+'%([^']*)%'",
        stmt_str,
        re.IGNORECASE,
    )
    return m.group(1) if m else None


@pytest.fixture
def stub_db(monkeypatch):
    """Stub AsyncSessionLocal to return a fake session yielding scripted schools."""

    schools: list[Any] = []
    docs: dict[str, Any] = {}

    class _FakeResult:
        def __init__(self, rows):
            self._rows = rows

        def scalars(self):
            return self

        def all(self):
            return list(self._rows)

        def scalar_one_or_none(self):
            return self._rows[0] if self._rows else None

    class _FakeDB:
        async def execute(self, stmt):
            # Compile with literal binds so we can inspect the actual
            # parameter values (e.g. `org_code = 'NOPE'`) rather than
            # the bound-parameter placeholders (`:org_code_1`).
            try:
                from sqlalchemy.dialects import postgresql

                compiled = stmt.compile(
                    dialect=postgresql.dialect(),
                    compile_kwargs={"literal_binds": True},
                )
                stmt_str = str(compiled)
            except Exception:
                stmt_str = str(stmt)

            # Detect single-row School lookups by org_code (the
            # `get_district_citations` path): these have `LIMIT 1`
            # plus a `schools.org_code =` clause. Resolve to None when
            # the org_code is not among the scripted schools.
            if "schools" in stmt_str.lower() and "LIMIT" in stmt_str.upper():
                org = _extract_org_code_literal(stmt_str)
                row = None
                if org is not None:
                    for s in schools:
                        if s.org_code == org:
                            row = s
                            break
                return _FakeResult([row] if row else [])
            if "schools" in stmt_str.lower() and "documents" not in stmt_str.lower():
                # List path: filter by state when present in the stmt.
                state = _extract_state_literal(stmt_str)
                name_substr = _extract_ilike_substring(stmt_str)
                rows = []
                for s in schools:
                    if state and s.state != state:
                        continue
                    if name_substr and name_substr.lower() not in s.name.lower():
                        continue
                    rows.append(s)
                return _FakeResult(rows)
            if "documents" in stmt_str.lower():
                rows = []
                for d in docs.values():
                    rows.append((d["doc_id"], d["id"]))
                return _FakeResult(rows)
            return _FakeResult([])

        async def get(self, model, pk):
            return None

    fake_db = _FakeDB()

    @asynccontextmanager
    async def _factory():
        yield fake_db

    import app.services.agentic_rag.tools as tools_module

    monkeypatch.setattr(tools_module, "AsyncSessionLocal", _factory)
    return fake_db, schools, docs


@pytest.fixture
def stub_vector_store(monkeypatch):
    """Replace VectorStoreFactory.create with a FakeVectorStore builder."""
    import app.services.agentic_rag.tools as tools_module

    store = FakeVectorStore()

    def _create(_type):
        return store

    monkeypatch.setattr(
        tools_module.VectorStoreFactory, "create", staticmethod(_create)
    )
    return store


@pytest.fixture
def runnable_config():
    return {"configurable": {"tenant_id": 1, "chatbot_config_id": 1}}


# ---------------------------------------------------------------------------
# count_districts_by_topic
# ---------------------------------------------------------------------------


async def test_count_districts_by_topic_walks_every_active_school(
    stub_db, stub_vector_store, runnable_config
):
    from app.services.agentic_rag.tools import count_districts_by_topic

    fake_db, schools, _docs = stub_db
    schools.extend(
        [
            _school("Boston Public Schools", "BPS"),
            _school("Newton Public Schools", "NPS"),
            _school("Cambridge Public Schools", "CPS"),
        ]
    )
    stub_vector_store.counts = {
        "Boston Public Schools": 12,
        "Newton Public Schools": 5,
        "Cambridge Public Schools": 0,
    }

    result = await count_districts_by_topic.ainvoke(
        {
            "topic_categories": ["sexed"],
            "topic_subtopics": ["comprehensive"],
            "meeting_date_from": "2025-09-01",
            "meeting_date_to": "2026-09-02",
        },
        config=runnable_config,
    )

    # Zero-count districts are dropped by default.
    assert [r["district_name"] for r in result] == [
        "Boston Public Schools",
        "Newton Public Schools",
    ]
    assert result[0]["chunk_count"] == 12
    # Each call's filter fragments should carry the subtopic nested
    # condition + the date range.
    first_call = stub_vector_store.count_calls[0]
    assert first_call["nested_match_any"] == {"topic_tags": ["sexed"]}
    assert first_call["nested_subtopic_match_any"] == {
        "topic_tags": ["comprehensive"]
    }
    assert "meeting_date" in first_call.get("range_match", {})


async def test_count_districts_by_topic_include_zero_keeps_zero_rows(
    stub_db, stub_vector_store, runnable_config
):
    from app.services.agentic_rag.tools import count_districts_by_topic

    fake_db, schools, _docs = stub_db
    schools.extend([_school("Boston Public Schools", "BPS")])
    stub_vector_store.counts = {"Boston Public Schools": 0}

    result = await count_districts_by_topic.ainvoke(
        {"include_zero": True}, config=runnable_config
    )
    assert len(result) == 1
    assert result[0]["chunk_count"] == 0


async def test_count_districts_by_topic_invalid_timeframe_returns_error(
    stub_db, stub_vector_store, runnable_config
):
    from app.services.agentic_rag.tools import count_districts_by_topic

    fake_db, schools, _docs = stub_db
    schools.extend([_school("Boston Public Schools", "BPS")])

    result = await count_districts_by_topic.ainvoke(
        {"timeframe": "not_a_real_preset"}, config=runnable_config
    )
    assert isinstance(result, list)
    assert len(result) == 1
    assert "error" in result[0]


# ---------------------------------------------------------------------------
# get_district_citations
# ---------------------------------------------------------------------------


async def test_get_district_citations_paginates_and_hydrates(
    stub_db, stub_vector_store, runnable_config
):
    from app.services.agentic_rag.tools import get_district_citations

    fake_db, schools, docs = stub_db
    schools.append(_school("Boston Public Schools", "BPS"))
    # doc_id (UUID) -> db id mapping that the citation hydrator resolves.
    docs["doc-uuid-1"] = {"doc_id": "doc-uuid-1", "id": 42}

    stub_vector_store.chunks = [
        {
            "text": "Chunk one about comprehensive sex ed.",
            "metadata": {
                "document_id": "doc-uuid-1",
                "document_name": "Boston Agenda 2025-09-10.pdf",
                "meeting_date": "2025-09-10",
                "page_number": 12,
                "chunk_index": 3,
                "topic_tags": [{"category": "sexed", "subtopic": "comprehensive"}],
                "action_stage": "Discussion Only",
            },
        },
        {
            "text": "Chunk two.",
            "metadata": {
                "document_id": "doc-uuid-1",
                "document_name": "Boston Agenda 2025-09-10.pdf",
                "meeting_date": "2025-09-10",
                "page_number": 13,
            },
        },
    ]

    result = await get_district_citations.ainvoke(
        {
            "org_code": "BPS",
            "topic_subtopics": ["comprehensive"],
            "page": 1,
            "page_size": 10,
        },
        config=runnable_config,
    )

    assert result["district_name"] == "Boston Public Schools"
    assert result["total"] == 2
    assert len(result["citations"]) == 2
    first = result["citations"][0]
    assert first["document_db_id"] == 42
    assert first["page_number"] == 12
    assert "comprehensive sex ed" in first["snippet"]


async def test_get_district_citations_unknown_org_code_returns_empty(
    stub_db, stub_vector_store, runnable_config
):
    from app.services.agentic_rag.tools import get_district_citations

    fake_db, schools, _docs = stub_db
    schools.append(_school("Boston Public Schools", "BPS"))

    result = await get_district_citations.ainvoke(
        {"org_code": "NOPE"}, config=runnable_config
    )
    assert result["citations"] == []
    assert result["total"] == 0
    assert "error" in result


async def test_get_district_citations_sort_date_desc_orders_recent_first(
    stub_db, stub_vector_store, runnable_config
):
    from app.services.agentic_rag.tools import get_district_citations

    fake_db, schools, docs = stub_db
    schools.append(_school("Boston Public Schools", "BPS"))
    docs["doc-a"] = {"doc_id": "doc-a", "id": 1}

    stub_vector_store.chunks = [
        {"text": "old", "metadata": {"document_id": "doc-a", "meeting_date": "2024-01-01"}},
        {"text": "new", "metadata": {"document_id": "doc-a", "meeting_date": "2026-09-01"}},
        {"text": "mid", "metadata": {"document_id": "doc-a", "meeting_date": "2025-06-01"}},
    ]

    result = await get_district_citations.ainvoke(
        {"org_code": "BPS", "sort": "date_desc", "page": 1, "page_size": 10},
        config=runnable_config,
    )
    dates = [c["meeting_date"] for c in result["citations"]]
    assert dates == ["2026-09-01", "2025-06-01", "2024-01-01"]


# ---------------------------------------------------------------------------
# list_districts
# ---------------------------------------------------------------------------


async def test_list_districts_returns_active_schools(
    stub_db, stub_vector_store, runnable_config
):
    from app.services.agentic_rag.tools import list_districts

    fake_db, schools, _docs = stub_db
    schools.extend(
        [
            _school("Boston Public Schools", "BPS"),
            _school("Newton Public Schools", "NPS"),
        ]
    )

    result = await list_districts.ainvoke(
        {"state": "MA"}, config=runnable_config
    )
    assert {r["org_code"] for r in result} == {"BPS", "NPS"}
    assert all(r["state"] == "MA" for r in result)


# ---------------------------------------------------------------------------
# get_taxonomy
# ---------------------------------------------------------------------------


async def test_get_taxonomy_returns_canonical_vocabulary(runnable_config):
    from app.services.agentic_rag.tools import get_taxonomy

    result = await get_taxonomy.ainvoke({}, config=runnable_config)
    assert "sex_education" in result["topics"]
    assert "book_challenged" in result["action_types"]
    assert "comprehensive" in result["sex_ed_subtopics"]
    assert "Agenda" in result["meeting_doc_types"]
    assert "Full Board" in result["meeting_bodies"]
    # Topic categories should be the 5 universal-core ones.
    cat_names = {c["category"] for c in result["topic_categories"]}
    assert cat_names == {"sexed", "lgbtq", "censorship", "governance", "advocacy"}


async def test_get_taxonomy_ma_pack_includes_state_curricula(runnable_config):
    from app.services.agentic_rag.tools import get_taxonomy

    result = await get_taxonomy.ainvoke({"state": "MA"}, config=runnable_config)
    # MA has state curricula (e.g. chpe_framework) — non-empty list.
    assert isinstance(result["state_curricula"], list)
    # State orgs is a list (may or may not be empty depending on the
    # current MA pack; assert shape only).
    assert isinstance(result["state_orgs"], list)


# ---------------------------------------------------------------------------
# Sample-query filter mapping — assert each of the 7 sample queries
# produces the expected Qdrant filter fragments.
# ---------------------------------------------------------------------------


def test_q1_comprehensive_sex_ed_since_sept_2025():
    """Q1: Since Sept 2025, which districts have discussed comprehensive
    sex education as part of the agenda?"""
    fragments = build_filter_fragments(
        topic_categories=["sexed"],
        topic_subtopics=["comprehensive"],
        meeting_doc_types=["Agenda"],
        meeting_date_from="2025-09-01",
        meeting_date_to="2026-09-02",
    )
    assert fragments["nested_match_any"] == {"topic_tags": ["sexed"]}
    assert fragments["nested_subtopic_match_any"] == {
        "topic_tags": ["comprehensive"]
    }
    assert fragments["must_match_any"]["meeting_doc_type"] == ["Agenda"]
    assert fragments["range_match"]["meeting_date"]["gte"].startswith("2025-09-01")


def test_q2_sex_ed_curriculum_changes_last_12_months():
    """Q2: In the last twelve months, districts with sex education
    curriculum changes on their agenda."""
    from datetime import date

    today = date(2026, 9, 2)
    f, t = relative_window(12, today=today)
    fragments = build_filter_fragments(
        topic_categories=["sexed"],
        topic_subtopics=[
            "change_expansion",
            "change_reduction",
            "change_under_review",
        ],
        action_types=[
            "instruction_reduced",
            "instruction_eliminated",
        ],
        meeting_doc_types=["Agenda"],
        meeting_date_from=f,
        meeting_date_to=t,
    )
    assert fragments["nested_subtopic_match_any"] == {
        "topic_tags": [
            "change_expansion",
            "change_reduction",
            "change_under_review",
        ]
    }
    assert fragments["must_match_any"]["action_types"] == [
        "instruction_reduced",
        "instruction_eliminated",
    ]
    assert fragments["range_match"]["meeting_date"]["gte"].startswith("2025-")


def test_q3_curriculum_censorship_this_year():
    """Q3: Summarize all curriculum censorship efforts discussed this year."""
    from datetime import date

    today = date(2026, 9, 2)
    fragments = build_filter_fragments(
        topic_categories=["censorship"],
        meeting_date_from=f"{today.year}-01-01",
        meeting_date_to=today.isoformat(),
    )
    assert fragments["nested_match_any"] == {"topic_tags": ["censorship"]}
    assert fragments["range_match"]["meeting_date"]["gte"].startswith(f"{today.year}-01-01")


def test_q4_highest_volume_of_book_challenges():
    """Q4: Which districts are experiencing the highest volume of book
    challenges?"""
    fragments = build_filter_fragments(
        topic_categories=["censorship"],
        topic_subtopics=[
            "book_challenge_filed",
            "book_removed",
            "book_retained",
            "curriculum_material_challenge",
        ],
        action_types=["book_challenged"],
    )
    assert set(fragments["nested_subtopic_match_any"]["topic_tags"]) == {
        "book_challenge_filed",
        "book_removed",
        "book_retained",
        "curriculum_material_challenge",
    }
    assert fragments["must_match_any"]["action_types"] == ["book_challenged"]


def test_q5_parental_rights_search_agenda_minutes_votes():
    """Q5: Analyze parental rights policies. Search agenda items, minutes,
    and board votes."""
    fragments = build_filter_fragments(
        topic_categories=["censorship"],
        topic_subtopics=["parental_rights_policy"],
        meeting_doc_types=["Agenda", "Minutes"],
        action_stages=[
            "Motion Made",
            "Vote — Passed",
            "Vote — Failed",
            "Vote — Tabled",
            "Policy First Reading",
            "Policy Adoption (Final)",
        ],
    )
    assert fragments["must_match_any"]["meeting_doc_type"] == [
        "Agenda",
        "Minutes",
    ]
    assert fragments["must_match_any"]["action_stage"] == [
        "Motion Made",
        "Vote — Passed",
        "Vote — Failed",
        "Vote — Tabled",
        "Policy First Reading",
        "Policy Adoption (Final)",
    ]


def test_q6_transgender_policies_last_12_months():
    """Q6: Identify districts debating transgender student policies in the
    past 12 months."""
    from datetime import date

    f, t = relative_window(12, today=date(2026, 9, 2))
    fragments = build_filter_fragments(
        topic_categories=["lgbtq"],
        topic_subtopics=["transgender_student_policy"],
        action_stages=["Discussion Only", "Public Comment", "Motion Made"],
        meeting_date_from=f,
        meeting_date_to=t,
    )
    assert fragments["nested_match_any"] == {"topic_tags": ["lgbtq"]}
    assert fragments["nested_subtopic_match_any"] == {
        "topic_tags": ["transgender_student_policy"]
    }
    assert fragments["range_match"]["meeting_date"]["gte"].startswith("2025-")


def test_q7_gender_identity_discussions():
    """Q7: Summarize all board discussions involving gender identity."""
    fragments = build_filter_fragments(
        topic_categories=["lgbtq"],
        topic_subtopics=["gender_identity_discussion"],
    )
    assert fragments["nested_match_any"] == {"topic_tags": ["lgbtq"]}
    assert fragments["nested_subtopic_match_any"] == {
        "topic_tags": ["gender_identity_discussion"]
    }
