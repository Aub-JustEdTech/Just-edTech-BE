"""Run the 7 sample district-analytics queries against the live Qdrant
corpus (tenant 2) using the new agentic RAG tools directly.

Bypasses the LLM — we call the tools with the filter sets the agent would
choose given the prompt, so this validates the retrieval path
end-to-end. Each query produces:
  1. A `count_districts_by_topic(...)` call → ranked district list
  2. For the top 1-2 districts, a `get_district_citations(...)` call
     → representative snippets

Run:
    POSTGRES_SERVER=localhost poetry run python scripts/run_sample_queries.py
"""

from __future__ import annotations

import asyncio
import json
import os
from typing import Any

# Force the host-side Postgres override so AsyncSessionLocal can reach
# the locally-running Postgres (the docker compose services use
# host.docker.internal, which doesn't resolve from a host shell).
os.environ.setdefault("POSTGRES_SERVER", "localhost")

from app.services.agentic_rag.tools import (  # noqa: E402
    count_districts_by_topic,
    get_district_citations,
    get_taxonomy,
    list_districts,
)

TENANT_ID = 2
CONFIG: dict[str, Any] = {
    "configurable": {"tenant_id": TENANT_ID, "chatbot_config_id": 1}
}

# Today's date for relative windows. The queries are being run on
# 2026-09-02 — "last 12 months" → 2025-09-02 onwards.
TODAY = "2026-09-02"
LAST_12_FROM = "2025-09-02"
THIS_YEAR_FROM = "2026-01-01"


def _fmt_count(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "  (no districts matched)"
    if isinstance(rows[0], dict) and "error" in rows[0]:
        return f"  ERROR: {rows[0]['error']}"
    out = []
    for r in rows[:10]:
        out.append(
            f"  - {r.get('district_name', '?')} "
            f"({r.get('state', '?')}, org_code={r.get('org_code', '?')}): "
            f"{r.get('chunk_count', 0)} chunks"
        )
    return "\n".join(out)


def _fmt_citations(resp: dict[str, Any]) -> str:
    if resp.get("error"):
        return f"  ERROR: {resp['error']}"
    district = resp.get("district_name")
    total = resp.get("total", 0)
    cites = resp.get("citations", [])
    out = [f"  District: {district} | total chunks: {total}"]
    for c in cites[:3]:
        date = c.get("meeting_date") or "?"
        doc = c.get("document_name") or "?"
        page = c.get("page_number")
        stage = c.get("action_stage")
        snippet = (c.get("snippet") or "").replace("\n", " ")[:160]
        tags = ", ".join(
            f"{t.get('category')}.{t.get('subtopic')}"
            for t in (c.get("topic_tags") or [])
            if isinstance(t, dict)
        )
        stage_str = f" [{stage}]" if stage else ""
        page_str = f" p.{page}" if page else ""
        out.append(
            f"    • {doc} ({date}){page_str}{stage_str}"
            + (f" tags=[{tags}]" if tags else "")
        )
        out.append(f"        \"{snippet}\"")
    return "\n".join(out)


async def query_1_comprehensive_sex_ed_since_sept_2025() -> None:
    print("\n" + "=" * 78)
    print("Q1. Since Sept 2025, which districts have discussed comprehensive")
    print("    sex education as part of the agenda?")
    print("=" * 78)
    # Use coarse `sex_education` topic + Agenda doc type + date range,
    # plus the fine `sexed` category as a parallel filter via topics.
    # (topic_subtopics=["comprehensive"] returns 0 — classifier didn't
    # use that exact label; coarse `sex_education` has only 2 chunks
    # but `topic_categories=["sexed"]` has 6.)
    filters = {
        "topic_categories": ["sexed"],
        "meeting_doc_types": ["Agenda"],
        "meeting_date_from": "2025-09-01",
        "meeting_date_to": TODAY,
    }
    rows = await count_districts_by_topic.ainvoke(filters, config=CONFIG)
    print("count_districts_by_topic:")
    print(_fmt_count(rows))
    # Drill into top 1-2 districts for citations.
    for row in rows[:2]:
        if "error" in row:
            continue
        cite = await get_district_citations.ainvoke(
            {
                "org_code": row["org_code"],
                **filters,
                "page_size": 3,
                "sort": "date_desc",
            },
            config=CONFIG,
        )
        print(_fmt_citations(cite))


async def query_2_sex_ed_curriculum_changes_last_12_months() -> None:
    print("\n" + "=" * 78)
    print("Q2. In the last twelve months, identify any districts with sex")
    print("    education curriculum changes on their agenda.")
    print("=" * 78)
    # "curriculum changes" → action_types instruction_reduced/eliminated
    # OR topic_categories=sexed + action_stages indicating change
    # (Motion Made, Vote, Policy First Reading, Policy Adoption).
    filters = {
        "topic_categories": ["sexed"],
        "meeting_doc_types": ["Agenda"],
        "action_stages": [
            "Motion Made",
            "Vote — Passed",
            "Vote — Failed",
            "Vote — Tabled",
            "Policy First Reading",
            "Policy Adoption (Final)",
        ],
        "meeting_date_from": LAST_12_FROM,
        "meeting_date_to": TODAY,
    }
    rows = await count_districts_by_topic.ainvoke(filters, config=CONFIG)
    print("count_districts_by_topic (sexed + agenda + change-stages + 12mo):")
    print(_fmt_count(rows))
    # Also try the action_types-only path (instruction_reduced/eliminated).
    filters2 = {
        "action_types": ["instruction_reduced", "instruction_eliminated"],
        "meeting_doc_types": ["Agenda"],
        "meeting_date_from": LAST_12_FROM,
        "meeting_date_to": TODAY,
    }
    rows2 = await count_districts_by_topic.ainvoke(filters2, config=CONFIG)
    print("count_districts_by_topic (action_types=instruction_reduced/eliminated):")
    print(_fmt_count(rows2))
    # Drill into the larger of the two result sets.
    drill_rows = rows if len(rows) > len(rows2) else rows2
    for row in drill_rows[:1]:
        if "error" in row:
            continue
        cite = await get_district_citations.ainvoke(
            {
                "org_code": row["org_code"],
                **(filters if drill_rows is rows else filters2),
                "page_size": 3,
                "sort": "date_desc",
            },
            config=CONFIG,
        )
        print(_fmt_citations(cite))


async def query_3_curriculum_censorship_this_year() -> None:
    print("\n" + "=" * 78)
    print("Q3. Summarize all curriculum censorship efforts discussed this year.")
    print("=" * 78)
    filters = {
        "topics": ["curriculum_censorship"],
        "meeting_date_from": THIS_YEAR_FROM,
        "meeting_date_to": TODAY,
    }
    rows = await count_districts_by_topic.ainvoke(filters, config=CONFIG)
    print("count_districts_by_topic (curriculum_censorship + this year):")
    print(_fmt_count(rows))
    # Also try the finer topic_categories=["censorship"].
    filters2 = {"topic_categories": ["censorship"], "meeting_date_from": THIS_YEAR_FROM, "meeting_date_to": TODAY}
    rows2 = await count_districts_by_topic.ainvoke(filters2, config=CONFIG)
    print("count_districts_by_topic (topic_categories=censorship + this year):")
    print(_fmt_count(rows2))
    drill_rows = rows if len(rows) > len(rows2) else rows2
    for row in drill_rows[:2]:
        if "error" in row:
            continue
        cite = await get_district_citations.ainvoke(
            {
                "org_code": row["org_code"],
                **(filters if drill_rows is rows else filters2),
                "page_size": 3,
                "sort": "date_desc",
            },
            config=CONFIG,
        )
        print(_fmt_citations(cite))


async def query_4_highest_volume_book_challenges() -> None:
    print("\n" + "=" * 78)
    print("Q4. Which districts are experiencing the highest volume of book")
    print("    challenges?")
    print("=" * 78)
    # action_types=book_challenged is the most direct signal.
    filters = {"action_types": ["book_challenged"]}
    rows = await count_districts_by_topic.ainvoke(filters, config=CONFIG)
    print("count_districts_by_topic (action_types=[book_challenged]):")
    print(_fmt_count(rows))
    # Also try topic_categories=censorship + subtopics for book challenges.
    filters2 = {
        "topic_categories": ["censorship"],
        "topic_subtopics": [
            "book_challenge_filed",
            "book_removed",
            "book_retained",
            "curriculum_material_challenge",
        ],
    }
    rows2 = await count_districts_by_topic.ainvoke(filters2, config=CONFIG)
    print("count_districts_by_topic (censorship + book_* subtopics):")
    print(_fmt_count(rows2))
    # And the coarse curriculum_censorship topic.
    filters3 = {"topics": ["curriculum_censorship"]}
    rows3 = await count_districts_by_topic.ainvoke(filters3, config=CONFIG)
    print("count_districts_by_topic (topics=[curriculum_censorship]):")
    print(_fmt_count(rows3))
    # Drill into whichever returned data.
    for label, r in [
        ("book_challenged", rows),
        ("censorship+subtopics", rows2),
        ("curriculum_censorship", rows3),
    ]:
        for row in r[:1]:
            if "error" in row:
                continue
            cite = await get_district_citations.ainvoke(
                {
                    "org_code": row["org_code"],
                    **(
                        filters
                        if label == "book_challenged"
                        else filters2
                        if label == "censorship+subtopics"
                        else filters3
                    ),
                    "page_size": 3,
                    "sort": "date_desc",
                },
                config=CONFIG,
            )
            print(f"  [{label}]")
            print(_fmt_citations(cite))


async def query_5_parental_rights_agenda_minutes_votes() -> None:
    print("\n" + "=" * 78)
    print("Q5. Analyze any current discussions around parental rights policies.")
    print("    Search agenda items, minutes, and board votes.")
    print("=" * 78)
    # Use coarse parental_rights topic + Agenda/Minutes doc types +
    # vote-related action_stages.
    filters = {
        "topics": ["parental_rights"],
        "meeting_doc_types": ["Agenda", "Minutes"],
        "action_stages": [
            "Motion Made",
            "Vote — Passed",
            "Vote — Failed",
            "Vote — Tabled",
            "Policy First Reading",
            "Policy Adoption (Final)",
        ],
    }
    rows = await count_districts_by_topic.ainvoke(filters, config=CONFIG)
    print("count_districts_by_topic (parental_rights + Agenda/Minutes + vote stages):")
    print(_fmt_count(rows))
    # Also try the broader path (no action_stage restriction) so we
    # see ALL parental_rights discussion.
    filters2 = {
        "topics": ["parental_rights"],
        "meeting_doc_types": ["Agenda", "Minutes"],
    }
    rows2 = await count_districts_by_topic.ainvoke(filters2, config=CONFIG)
    print("count_districts_by_topic (parental_rights + Agenda/Minutes, all stages):")
    print(_fmt_count(rows2))
    # Drill into top 2 from the broader path.
    for row in rows2[:2]:
        if "error" in row:
            continue
        cite = await get_district_citations.ainvoke(
            {"org_code": row["org_code"], **filters2, "page_size": 3, "sort": "date_desc"},
            config=CONFIG,
        )
        print(_fmt_citations(cite))


async def query_6_transgender_policies_last_12_months() -> None:
    print("\n" + "=" * 78)
    print("Q6. Identify districts debating transgender student policies in")
    print("    the past 12 months.")
    print("=" * 78)
    # Use coarse transgender_policy topic + date range. The finer
    # topic_subtopics=[transgender_student_policy] has only 16 chunks
    # total; combined with a 12-month window it may be too narrow.
    filters = {
        "topics": ["transgender_policy"],
        "meeting_date_from": LAST_12_FROM,
        "meeting_date_to": TODAY,
    }
    rows = await count_districts_by_topic.ainvoke(filters, config=CONFIG)
    print("count_districts_by_topic (transgender_policy + last 12 months):")
    print(_fmt_count(rows))
    # Also try the fine subtopic.
    filters2 = {
        "topic_subtopics": ["transgender_student_policy"],
        "meeting_date_from": LAST_12_FROM,
        "meeting_date_to": TODAY,
    }
    rows2 = await count_districts_by_topic.ainvoke(filters2, config=CONFIG)
    print("count_districts_by_topic (topic_subtopics=transgender_student_policy + 12mo):")
    print(_fmt_count(rows2))
    # And the broader lgbtq_student_rights coarse topic.
    filters3 = {
        "topics": ["lgbtq_student_rights"],
        "meeting_date_from": LAST_12_FROM,
        "meeting_date_to": TODAY,
    }
    rows3 = await count_districts_by_topic.ainvoke(filters3, config=CONFIG)
    print("count_districts_by_topic (lgbtq_student_rights + last 12 months):")
    print(_fmt_count(rows3))
    # Drill into the larger of the three.
    drill = max([rows, rows2, rows3], key=len)
    drill_filters = (
        filters
        if drill is rows
        else filters2
        if drill is rows2
        else filters3
    )
    for row in drill[:2]:
        if "error" in row:
            continue
        cite = await get_district_citations.ainvoke(
            {"org_code": row["org_code"], **drill_filters, "page_size": 3, "sort": "date_desc"},
            config=CONFIG,
        )
        print(_fmt_citations(cite))


async def query_7_gender_identity_discussions() -> None:
    print("\n" + "=" * 78)
    print("Q7. Summarize all board discussions involving gender identity.")
    print("=" * 78)
    # Coarse gender_identity topic — 78 chunks — is the most useful.
    filters = {"topics": ["gender_identity"]}
    rows = await count_districts_by_topic.ainvoke(filters, config=CONFIG)
    print("count_districts_by_topic (topics=[gender_identity]):")
    print(_fmt_count(rows))
    # Drill into top 2 districts for evidence.
    for row in rows[:2]:
        if "error" in row:
            continue
        cite = await get_district_citations.ainvoke(
            {"org_code": row["org_code"], **filters, "page_size": 3, "sort": "date_desc"},
            config=CONFIG,
        )
        print(_fmt_citations(cite))


async def main() -> None:
    print("Taxonomy check (first 5 categories):")
    tax = await get_taxonomy.ainvoke({"state": "MA"}, config=CONFIG)
    for cat in tax.get("topic_categories", [])[:2]:
        print(f"  {cat['category']}: {len(cat['subtopics'])} subtopics")
    print()

    print("District roster (first 5):")
    districts = await list_districts.ainvoke({"state": "MA"}, config=CONFIG)
    for d in districts[:5]:
        print(f"  {d['org_code']}: {d['district_name']} ({d['state']})")
    print(f"  ... ({len(districts)} total)")

    await query_1_comprehensive_sex_ed_since_sept_2025()
    await query_2_sex_ed_curriculum_changes_last_12_months()
    await query_3_curriculum_censorship_this_year()
    await query_4_highest_volume_book_challenges()
    await query_5_parental_rights_agenda_minutes_votes()
    await query_6_transgender_policies_last_12_months()
    await query_7_gender_identity_discussions()


if __name__ == "__main__":
    asyncio.run(main())
