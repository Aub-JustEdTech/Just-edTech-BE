"""Fixed Q1-Q7 query catalog for district analytics reports.

Each entry pins the canonical research question, the geography, and one or
more retrieval passes expressed as filter dictionaries (the same surface the
agent tools accept). Date windows are computed at runtime from "today" so
reports stay current without editing this file.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any


@dataclass(frozen=True)
class QuerySpec:
    """A fixed district-analytics report query."""

    query_id: str
    title: str
    research_goal: str
    question: str
    geography: str = "Massachusetts"
    # Each filter set is a retrieval pass. A callable lets us compute
    # date windows at runtime from `today`; a static dict is reused as-is.
    filter_sets: tuple[Callable[[date], dict[str, Any]], ...] = field(default_factory=tuple)


# ---------------------------------------------------------------------------
# Date window helpers (computed at runtime)
# ---------------------------------------------------------------------------

def _today() -> date:
    return date.today()


def _last_12_months(today: date) -> str:
    """ISO date one year before `today`."""
    return (today - timedelta(days=365)).isoformat()


def _year_start(today: date) -> str:
    """ISO date for Jan 1 of the current calendar year."""
    return date(today.year, 1, 1).isoformat()


def _today_iso(today: date) -> str:
    return today.isoformat()


# Change-related action stages for "curriculum changes" / policy changes.
CHANGE_ACTION_STAGES = (
    "Motion Made",
    "Vote — Passed",
    "Vote — Failed",
    "Vote — Tabled",
    "Policy First Reading",
    "Policy Adoption (Final)",
)

VOTE_ACTION_STAGES = (
    "Motion Made",
    "Vote — Passed",
    "Vote — Failed",
    "Vote — Tabled",
    "Policy First Reading",
    "Policy Adoption (Final)",
)


# ---------------------------------------------------------------------------
# Q1 — Comprehensive sex education on agendas since September 2025
# ---------------------------------------------------------------------------

def _q1_filters(_today: date) -> dict[str, Any]:
    return {
        "topic_categories": ["sexed"],
        "meeting_doc_types": ["Agenda"],
        "meeting_date_from": "2025-09-01",
    }


# ---------------------------------------------------------------------------
# Q2 — Sex education curriculum changes on the agenda, last 12 months
# ---------------------------------------------------------------------------

def _q2_filters_a(today: date) -> dict[str, Any]:
    return {
        "topic_categories": ["sexed"],
        "meeting_doc_types": ["Agenda"],
        "action_stages": list(CHANGE_ACTION_STAGES),
        "meeting_date_from": _last_12_months(today),
    }


def _q2_filters_b(today: date) -> dict[str, Any]:
    return {
        "action_types": ["instruction_reduced", "instruction_eliminated"],
        "meeting_doc_types": ["Agenda"],
        "meeting_date_from": _last_12_months(today),
    }


# ---------------------------------------------------------------------------
# Q3 — Curriculum censorship efforts discussed this calendar year
# ---------------------------------------------------------------------------

def _q3_filters_a(today: date) -> dict[str, Any]:
    return {
        "topics": ["curriculum_censorship"],
        "meeting_date_from": _year_start(today),
    }


def _q3_filters_b(today: date) -> dict[str, Any]:
    return {
        "topic_categories": ["censorship"],
        "meeting_date_from": _year_start(today),
    }


# ---------------------------------------------------------------------------
# Q4 — Districts with the highest volume of book challenges
# ---------------------------------------------------------------------------

def _q4_filters_a(_today: date) -> dict[str, Any]:
    return {"action_types": ["book_challenged"]}


def _q4_filters_b(_today: date) -> dict[str, Any]:
    return {
        "topic_categories": ["censorship"],
        "topic_subtopics": [
            "book_challenge_filed",
            "book_removed",
            "book_retained",
            "curriculum_material_challenge",
        ],
    }


def _q4_filters_c(_today: date) -> dict[str, Any]:
    return {"topics": ["curriculum_censorship"]}


# ---------------------------------------------------------------------------
# Q5 — Current discussions around parental rights policies
# (agenda items, minutes, board votes)
# ---------------------------------------------------------------------------

def _q5_filters_a(_today: date) -> dict[str, Any]:
    return {
        "topics": ["parental_rights"],
        "meeting_doc_types": ["Agenda", "Minutes"],
        "action_stages": list(VOTE_ACTION_STAGES),
    }


def _q5_filters_b(_today: date) -> dict[str, Any]:
    return {
        "topics": ["parental_rights"],
        "meeting_doc_types": ["Agenda", "Minutes"],
    }


# ---------------------------------------------------------------------------
# Q6 — Districts debating transgender student policies, last 12 months
# ---------------------------------------------------------------------------

def _q6_filters_a(today: date) -> dict[str, Any]:
    return {
        "topics": ["transgender_policy"],
        "meeting_date_from": _last_12_months(today),
    }


def _q6_filters_b(today: date) -> dict[str, Any]:
    return {
        "topic_subtopics": ["transgender_student_policy"],
        "meeting_date_from": _last_12_months(today),
    }


def _q6_filters_c(today: date) -> dict[str, Any]:
    return {
        "topics": ["lgbtq_student_rights"],
        "meeting_date_from": _last_12_months(today),
    }


# ---------------------------------------------------------------------------
# Q7 — Board discussions involving gender identity
# ---------------------------------------------------------------------------

def _q7_filters(_today: date) -> dict[str, Any]:
    return {"topics": ["gender_identity"]}


# ---------------------------------------------------------------------------
# Catalog
# ---------------------------------------------------------------------------

QUERIES: dict[str, QuerySpec] = {
    "Q1": QuerySpec(
        query_id="Q1",
        title="Comprehensive Sex Education on Agendas Since September 2025",
        research_goal=(
            "Identify Massachusetts school districts that have placed "
            "comprehensive sex education on a school committee agenda "
            "since September 2025."
        ),
        question=(
            "Since Sept 2025, which districts have discussed comprehensive "
            "sex education as part of the agenda?"
        ),
        filter_sets=(_q1_filters,),
    ),
    "Q2": QuerySpec(
        query_id="Q2",
        title="Sex Education Curriculum Changes on Agendas, Last 12 Months",
        research_goal=(
            "Identify Massachusetts school districts with sex education "
            "curriculum changes on their agenda in the last twelve months."
        ),
        question=(
            "In the last twelve months, identify any districts with sex "
            "education curriculum changes on their agenda."
        ),
        filter_sets=(_q2_filters_a, _q2_filters_b),
    ),
    "Q3": QuerySpec(
        query_id="Q3",
        title="Curriculum Censorship Efforts Discussed This Year",
        research_goal=(
            "Summarize all curriculum censorship efforts discussed in "
            "Massachusetts school board meetings this calendar year."
        ),
        question="Summarize all curriculum censorship efforts discussed this year.",
        filter_sets=(_q3_filters_a, _q3_filters_b),
    ),
    "Q4": QuerySpec(
        query_id="Q4",
        title="Districts With the Highest Volume of Book Challenges",
        research_goal=(
            "Identify the Massachusetts school districts experiencing the "
            "highest volume of book challenges."
        ),
        question="Which districts are experiencing the highest volume of book challenges?",
        filter_sets=(_q4_filters_a, _q4_filters_b, _q4_filters_c),
    ),
    "Q5": QuerySpec(
        query_id="Q5",
        title="Current Discussions Around Parental Rights Policies",
        research_goal=(
            "Analyze current Massachusetts school board discussions around "
            "parental rights policies, including agenda items, minutes, "
            "and board votes."
        ),
        question=(
            "Analyze any current discussions around parental rights "
            "policies. Search agenda items, minutes, and board votes."
        ),
        filter_sets=(_q5_filters_a, _q5_filters_b),
    ),
    "Q6": QuerySpec(
        query_id="Q6",
        title="Districts Debating Transgender Student Policies, Last 12 Months",
        research_goal=(
            "Identify Massachusetts school districts debating transgender "
            "student policies in the past twelve months."
        ),
        question=(
            "Identify districts debating transgender student policies in "
            "the past 12 months."
        ),
        filter_sets=(_q6_filters_a, _q6_filters_b, _q6_filters_c),
    ),
    "Q7": QuerySpec(
        query_id="Q7",
        title="Board Discussions Involving Gender Identity",
        research_goal=(
            "Summarize all Massachusetts school board discussions involving "
            "gender identity."
        ),
        question="Summarize all board discussions involving gender identity.",
        filter_sets=(_q7_filters,),
    ),
}


def list_query_ids() -> list[str]:
    """Return the ordered list of supported fixed query IDs."""
    return list(QUERIES.keys())


def get_query_spec(query_id: str) -> QuerySpec:
    """Look up a query spec by ID. Raises ValueError if unknown."""
    spec = QUERIES.get(query_id)
    if spec is None:
        raise ValueError(
            f"Unknown query_id {query_id!r}. Supported: {list(QUERIES)}"
        )
    return spec


def resolve_filters(spec: QuerySpec, today: date | None = None) -> list[dict[str, Any]]:
    """Materialize each filter set into a concrete dict for retrieval."""
    today = today or _today()
    return [builder(today) for builder in spec.filter_sets]
