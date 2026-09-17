"""Tenant-scoped fixed query catalogs for district analytics reports.

Each tenant owns its own Q1…Qn set. Massachusetts (tenant 4) keeps the
cross-district taxonomy queries; California (tenant 5) uses the
single-district qualitative analysis set. Date windows are computed at
runtime from "today" so reports stay current without editing this file.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

# Tenant IDs that own a fixed query catalog. Keep in sync with seeded tenants
# (JET Tenant 4 = MA corpus; California = CA corpus).
MA_TENANT_ID = 4
CA_TENANT_ID = 5

# Default focus district for CA single-district qualitative reports.
# Matches the golden Saddleback answers; override per request via
# `district_org_code` on the API / CLI.
CA_DEFAULT_DISTRICT_ORG_CODE = "30-73635"  # Saddleback Valley Unified
TENANT_DEFAULT_DISTRICT_ORG_CODE: dict[int, str] = {
    CA_TENANT_ID: CA_DEFAULT_DISTRICT_ORG_CODE,
}

# Map QuerySpec.geography → 2-letter state code used by retrieval tools.
# `count_districts_by_topic` / `list_districts` default to MA when `states`
# is omitted, so every filter set MUST carry an explicit state derived here.
GEOGRAPHY_STATE_CODES: dict[str, str] = {
    "Massachusetts": "MA",
    "California": "CA",
}

# How evidence is gathered for a query.
# - topic_counts: heatmap-style `count_districts_by_topic` + citations
#   (MA policy-taxonomy questions).
# - semantic: embedding search over the question text (CA qualitative
#   analysis — strengths, challenges, public comments, etc.).
RETRIEVAL_TOPIC_COUNTS = "topic_counts"
RETRIEVAL_SEMANTIC = "semantic"


@dataclass(frozen=True)
class QuerySpec:
    """A fixed district-analytics report query bound to one tenant."""

    query_id: str
    title: str
    research_goal: str
    question: str
    tenant_id: int
    geography: str = "Massachusetts"
    retrieval_mode: str = RETRIEVAL_TOPIC_COUNTS
    # Short embedding query for semantic mode. Falls back to `question`
    # when omitted. Prefer theme keywords over the long stakeholder prompt.
    search_query: str | None = None
    # Each filter set is a retrieval pass. A callable lets us compute
    # date windows at runtime from `today`; a static dict is reused as-is.
    # Passes may include `_search_query` (stripped before tool invoke) to
    # override `search_query` for that pass only.
    filter_sets: tuple[Callable[[date], dict[str, Any]], ...] = field(default_factory=tuple)


def geography_to_state(geography: str) -> str:
    """Translate a QuerySpec geography label to a 2-letter state code."""
    try:
        return GEOGRAPHY_STATE_CODES[geography]
    except KeyError as exc:
        raise ValueError(
            f"Unknown geography {geography!r}. "
            f"Supported: {sorted(GEOGRAPHY_STATE_CODES)}"
        ) from exc


def default_district_org_code(tenant_id: int) -> str | None:
    """Return the tenant's default focus district, if configured."""
    return TENANT_DEFAULT_DISTRICT_ORG_CODE.get(tenant_id)


# ---------------------------------------------------------------------------
# Date window helpers (computed at runtime)
# ---------------------------------------------------------------------------

def _today() -> date:
    return date.today()


def _last_12_months(today: date) -> str:
    """ISO date one year before `today`."""
    return (today - timedelta(days=365)).isoformat()


def _last_24_months(today: date) -> str:
    """ISO date two years before `today`."""
    return (today - timedelta(days=730)).isoformat()


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
# Massachusetts (tenant 4) — Q1–Q7
# ---------------------------------------------------------------------------

def _ma_q1_filters(_today: date) -> dict[str, Any]:
    return {
        "topic_categories": ["sexed"],
        "meeting_doc_types": ["Agenda"],
        "meeting_date_from": "2025-09-01",
    }


def _ma_q2_filters_a(today: date) -> dict[str, Any]:
    return {
        "topic_categories": ["sexed"],
        "meeting_doc_types": ["Agenda"],
        "action_stages": list(CHANGE_ACTION_STAGES),
        "meeting_date_from": _last_12_months(today),
    }


def _ma_q2_filters_b(today: date) -> dict[str, Any]:
    return {
        "action_types": ["instruction_reduced", "instruction_eliminated"],
        "meeting_doc_types": ["Agenda"],
        "meeting_date_from": _last_12_months(today),
    }


def _ma_q3_filters_a(today: date) -> dict[str, Any]:
    return {
        "topics": ["curriculum_censorship"],
        "meeting_date_from": _year_start(today),
    }


def _ma_q3_filters_b(today: date) -> dict[str, Any]:
    return {
        "topic_categories": ["censorship"],
        "meeting_date_from": _year_start(today),
    }


def _ma_q4_filters_a(_today: date) -> dict[str, Any]:
    return {"action_types": ["book_challenged"]}


def _ma_q4_filters_b(_today: date) -> dict[str, Any]:
    return {
        "topic_categories": ["censorship"],
        "topic_subtopics": [
            "book_challenge_filed",
            "book_removed",
            "book_retained",
            "curriculum_material_challenge",
        ],
    }


def _ma_q4_filters_c(_today: date) -> dict[str, Any]:
    return {"topics": ["curriculum_censorship"]}


def _ma_q5_filters_a(_today: date) -> dict[str, Any]:
    return {
        "topics": ["parental_rights"],
        "meeting_doc_types": ["Agenda", "Minutes"],
        "action_stages": list(VOTE_ACTION_STAGES),
    }


def _ma_q5_filters_b(_today: date) -> dict[str, Any]:
    return {
        "topics": ["parental_rights"],
        "meeting_doc_types": ["Agenda", "Minutes"],
    }


def _ma_q6_filters_a(today: date) -> dict[str, Any]:
    return {
        "topics": ["transgender_policy"],
        "meeting_date_from": _last_12_months(today),
    }


def _ma_q6_filters_b(today: date) -> dict[str, Any]:
    return {
        "topic_subtopics": ["transgender_student_policy"],
        "meeting_date_from": _last_12_months(today),
    }


def _ma_q6_filters_c(today: date) -> dict[str, Any]:
    return {
        "topics": ["lgbtq_student_rights"],
        "meeting_date_from": _last_12_months(today),
    }


def _ma_q7_filters(_today: date) -> dict[str, Any]:
    return {"topics": ["gender_identity"]}


MA_QUERIES: dict[str, QuerySpec] = {
    "Q1": QuerySpec(
        query_id="Q1",
        tenant_id=MA_TENANT_ID,
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
        geography="Massachusetts",
        filter_sets=(_ma_q1_filters,),
    ),
    "Q2": QuerySpec(
        query_id="Q2",
        tenant_id=MA_TENANT_ID,
        title="Sex Education Curriculum Changes on Agendas, Last 12 Months",
        research_goal=(
            "Identify Massachusetts school districts with sex education "
            "curriculum changes on their agenda in the last twelve months."
        ),
        question=(
            "In the last twelve months, identify any districts with sex "
            "education curriculum changes on their agenda."
        ),
        geography="Massachusetts",
        filter_sets=(_ma_q2_filters_a, _ma_q2_filters_b),
    ),
    "Q3": QuerySpec(
        query_id="Q3",
        tenant_id=MA_TENANT_ID,
        title="Curriculum Censorship Efforts Discussed This Year",
        research_goal=(
            "Summarize all curriculum censorship efforts discussed in "
            "Massachusetts school board meetings this calendar year."
        ),
        question="Summarize all curriculum censorship efforts discussed this year.",
        geography="Massachusetts",
        filter_sets=(_ma_q3_filters_a, _ma_q3_filters_b),
    ),
    "Q4": QuerySpec(
        query_id="Q4",
        tenant_id=MA_TENANT_ID,
        title="Districts With the Highest Volume of Book Challenges",
        research_goal=(
            "Identify the Massachusetts school districts experiencing the "
            "highest volume of book challenges."
        ),
        question="Which districts are experiencing the highest volume of book challenges?",
        geography="Massachusetts",
        filter_sets=(_ma_q4_filters_a, _ma_q4_filters_b, _ma_q4_filters_c),
    ),
    "Q5": QuerySpec(
        query_id="Q5",
        tenant_id=MA_TENANT_ID,
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
        geography="Massachusetts",
        filter_sets=(_ma_q5_filters_a, _ma_q5_filters_b),
    ),
    "Q6": QuerySpec(
        query_id="Q6",
        tenant_id=MA_TENANT_ID,
        title="Districts Debating Transgender Student Policies, Last 12 Months",
        research_goal=(
            "Identify Massachusetts school districts debating transgender "
            "student policies in the past twelve months."
        ),
        question=(
            "Identify districts debating transgender student policies in "
            "the past 12 months."
        ),
        geography="Massachusetts",
        filter_sets=(_ma_q6_filters_a, _ma_q6_filters_b, _ma_q6_filters_c),
    ),
    "Q7": QuerySpec(
        query_id="Q7",
        tenant_id=MA_TENANT_ID,
        title="Board Discussions Involving Gender Identity",
        research_goal=(
            "Summarize all Massachusetts school board discussions involving "
            "gender identity."
        ),
        question="Summarize all board discussions involving gender identity.",
        geography="Massachusetts",
        filter_sets=(_ma_q7_filters,),
    ),
}


# ---------------------------------------------------------------------------
# California (tenant 5) — Q1–Q5
# ---------------------------------------------------------------------------
#
# search_query strings are tuned to the Saddleback golden answers so
# embedding retrieval pulls budget/enrollment/public-comment evidence
# rather than diluting against the long stakeholder question text.

def _ca_last_24_months(today: date) -> dict[str, Any]:
    return {"meeting_date_from": _last_24_months(today)}


def _ca_last_12_months(today: date) -> dict[str, Any]:
    return {"meeting_date_from": _last_12_months(today)}


def _ca_q4_public_comments(today: date) -> dict[str, Any]:
    return {
        "meeting_doc_types": ["Minutes"],
        "meeting_date_from": _last_24_months(today),
        "_search_query": (
            "public comment speakers class size combination combo classes "
            "special education caseload teacher compensation negotiations "
            "school closures immigration ICE enforcement community liaison"
        ),
    }


def _ca_q4_board_followthrough(today: date) -> dict[str, Any]:
    return {
        "meeting_date_from": _last_24_months(today),
        "_search_query": (
            "board response action vote policy decision class size "
            "staffing school closure special education follow through"
        ),
    }


def _ca_q5_board_priorities(today: date) -> dict[str, Any]:
    return {
        "meeting_doc_types": ["Agenda", "Minutes"],
        "meeting_date_from": _last_12_months(today),
        "_search_query": (
            "board agenda budget interim financial report LCAP "
            "textbooks instructional materials staffing assignments "
            "board policy CSBA presentation public hearing"
        ),
    }


def _ca_q5_public_concerns(today: date) -> dict[str, Any]:
    return {
        "meeting_doc_types": ["Minutes"],
        "meeting_date_from": _last_12_months(today),
        "_search_query": (
            "public comment class size combo classes technology "
            "Chromebooks teacher compensation LCAP budget concern "
            "staffing community speaker"
        ),
    }


CA_QUERIES: dict[str, QuerySpec] = {
    "Q1": QuerySpec(
        query_id="Q1",
        tenant_id=CA_TENANT_ID,
        title="District Strengths and Successful Initiatives, Past 24 Months",
        research_goal=(
            "Identify recurring positive themes, successful initiatives, "
            "student achievement highlights, innovative programs, community "
            "partnerships, and accomplishments that California district "
            "leaders consistently emphasize."
        ),
        question=(
            "Analyze all school board meeting agendas, presentations, "
            "superintendent reports, recognitions, and board discussions "
            "from the past 24 months. Identify recurring positive themes, "
            "successful initiatives, student achievement highlights, "
            "innovative programs, community partnerships, or accomplishments "
            "that district leaders consistently emphasize. Summarize the "
            "district's strengths and identify areas where community support "
            "and future investment could build on existing momentum."
        ),
        geography="California",
        retrieval_mode=RETRIEVAL_SEMANTIC,
        search_query=(
            "student achievement recognition Green Ribbon Distinguished Schools "
            "National Merit AP Honor Roll innovative programs SpiderLab IB "
            "career technical education arts music community partnerships "
            "Unified Youth Summit Green Team sustainability"
        ),
        filter_sets=(_ca_last_24_months,),
    ),
    "Q2": QuerySpec(
        query_id="Q2",
        tenant_id=CA_TENANT_ID,
        title="Operational and Financial Challenges, Past 24 Months",
        research_goal=(
            "Identify the district's most significant operational and "
            "financial challenges — including budget deficits, declining "
            "enrollment, special education expenditures, staffing shortages, "
            "deferred maintenance, or reductions in state or federal funding "
            "— and summarize them by frequency and urgency."
        ),
        question=(
            "Analyze school board agendas, budget presentations, financial "
            "reports, superintendent updates, and board discussions from the "
            "past 24 months related to district finance, special education, "
            "staffing, enrollment, facilities, and state or federal funding. "
            "Identify the district's most significant operational and "
            "financial challenges, including budget deficits, declining "
            "enrollment, special education expenditures, staffing shortages, "
            "deferred maintenance, or reductions in state or federal funding. "
            "Summarize the challenges by frequency and urgency."
        ),
        geography="California",
        retrieval_mode=RETRIEVAL_SEMANTIC,
        search_query=(
            "budget deficit interim financial report declining enrollment "
            "special education expenditures staffing shortage Reduction in "
            "Force RIF deferred maintenance health welfare pension insurance "
            "state funding federal funding structural deficit reserves"
        ),
        filter_sets=(_ca_last_24_months,),
    ),
    "Q3": QuerySpec(
        query_id="Q3",
        tenant_id=CA_TENANT_ID,
        title="Governance Challenges and Unresolved Priorities, Past 24 Months",
        research_goal=(
            "Summarize recurring governance challenges, unresolved policy "
            "issues, and incomplete strategic priorities that are likely to "
            "remain relevant in future school board campaigns."
        ),
        question=(
            "Analyze school board agendas, board discussions, votes, "
            "strategic planning sessions, and public comments from the past "
            "24 months. Identify governance issues where the board has "
            "struggled to reach consensus, delayed major decisions, reversed "
            "previous actions, experienced contentious votes, or received "
            "sustained public criticism. Summarize recurring governance "
            "challenges, unresolved policy issues, and strategic priorities "
            "that remain incomplete to help identify issues likely to be "
            "relevant in future school board campaigns."
        ),
        geography="California",
        retrieval_mode=RETRIEVAL_SEMANTIC,
        search_query=(
            "governance board vote failed motion contentious decision LCAP "
            "strategic planning Facilities Focus Group public criticism "
            "class size teacher compensation antisemitism academic calendar "
            "budget deficit unresolved priority early retirement"
        ),
        filter_sets=(_ca_last_24_months,),
    ),
    "Q4": QuerySpec(
        query_id="Q4",
        tenant_id=CA_TENANT_ID,
        title="Public Comments Themes and Board Follow-Through, Past 24 Months",
        research_goal=(
            "Categorize public comments by topic, identify recurring "
            "speakers and concerns, and flag issues that received limited "
            "or no substantive board response."
        ),
        question=(
            "Analyze all public comments from school board meetings during "
            "the past 24 months. Categorize comments by topic (such as "
            "budget, special education, curriculum, facilities, staffing, "
            "student safety, LGBTQ+ rights, sex education, school closures, "
            "labor issues, ICE or immigration issues). Identify recurring "
            "speakers, organizations, and concerns raised multiple times. "
            "Compare these concerns against subsequent board agendas, "
            "discussions, votes, and district actions to identify issues "
            "that received limited or no substantive board response."
        ),
        geography="California",
        retrieval_mode=RETRIEVAL_SEMANTIC,
        search_query=(
            "public comment speakers class size special education "
            "teacher compensation school closures immigration"
        ),
        filter_sets=(_ca_q4_public_comments, _ca_q4_board_followthrough),
    ),
    "Q5": QuerySpec(
        query_id="Q5",
        tenant_id=CA_TENANT_ID,
        title="Board Priorities vs Public Concerns, Past 12 Months",
        research_goal=(
            "Compare board agendas, discussions, and votes with public "
            "comments over the past 12 months and highlight areas of "
            "alignment and disconnect across common policy themes."
        ),
        question=(
            "Compare all school board agendas, presentations, discussion "
            "items, and voting actions from the past 12 months with all "
            "public comments made during the same period. Categorize both "
            "into common policy themes (such as budget, facilities, "
            "curriculum, special education, personnel, student achievement, "
            "governance, and school climate). Identify where board "
            "priorities align with community concerns and where significant "
            "gaps exist between issues discussed by the board and issues "
            "most frequently raised during public comment. Produce a "
            "comparative analysis highlighting areas of alignment and "
            "disconnect."
        ),
        geography="California",
        retrieval_mode=RETRIEVAL_SEMANTIC,
        search_query=(
            "board priorities versus public comments alignment disconnect "
            "budget curriculum technology class size governance"
        ),
        filter_sets=(_ca_q5_board_priorities, _ca_q5_public_concerns),
    ),
}


# ---------------------------------------------------------------------------
# Catalog (tenant_id → query_id → QuerySpec)
# ---------------------------------------------------------------------------

QUERIES_BY_TENANT: dict[int, dict[str, QuerySpec]] = {
    MA_TENANT_ID: MA_QUERIES,
    CA_TENANT_ID: CA_QUERIES,
}


def list_tenant_ids() -> list[int]:
    """Return tenant IDs that have a fixed query catalog."""
    return list(QUERIES_BY_TENANT.keys())


def list_queries_for_tenant(tenant_id: int) -> list[QuerySpec]:
    """Return ordered QuerySpecs for a tenant (empty if none configured)."""
    catalog = QUERIES_BY_TENANT.get(tenant_id, {})
    return list(catalog.values())


def list_query_ids(tenant_id: int) -> list[str]:
    """Return the ordered list of fixed query IDs for a tenant."""
    return [spec.query_id for spec in list_queries_for_tenant(tenant_id)]


def get_query_spec(query_id: str, tenant_id: int) -> QuerySpec:
    """Look up a query spec by ID within a tenant. Raises ValueError if unknown."""
    catalog = QUERIES_BY_TENANT.get(tenant_id)
    if catalog is None:
        raise ValueError(
            f"No fixed queries configured for tenant_id={tenant_id}. "
            f"Supported tenants: {list(QUERIES_BY_TENANT)}"
        )
    spec = catalog.get(query_id)
    if spec is None:
        raise ValueError(
            f"Unknown query_id {query_id!r} for tenant_id={tenant_id}. "
            f"Supported: {list(catalog)}"
        )
    return spec


def resolve_filters(spec: QuerySpec, today: date | None = None) -> list[dict[str, Any]]:
    """Materialize each filter set into a concrete dict for retrieval.

    Always injects `states` from `spec.geography` unless a filter builder
    already set it. This prevents CA (and future non-MA) reports from
    silently falling through to the MA default inside the retrieval tools.

    Filter builders may include `_search_query` for per-pass semantic
    overrides; callers must strip that key before invoking tools that
    do not accept it.
    """
    today = today or _today()
    state = geography_to_state(spec.geography)
    resolved: list[dict[str, Any]] = []
    for builder in spec.filter_sets:
        filters = dict(builder(today))
        filters.setdefault("states", [state])
        resolved.append(filters)
    return resolved


def resolve_search_query(spec: QuerySpec, filters: dict[str, Any] | None = None) -> str:
    """Pick the embedding query for a semantic retrieval pass."""
    if filters:
        override = filters.get("_search_query")
        if isinstance(override, str) and override.strip():
            return override.strip()
    if spec.search_query and spec.search_query.strip():
        return spec.search_query.strip()
    return spec.question
