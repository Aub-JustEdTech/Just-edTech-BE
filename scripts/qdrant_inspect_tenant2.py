"""Inspect the Qdrant corpus for tenant 2 — full distribution of
topic_tags, topics, action_types, action_stage, meeting_doc_type,
district_name, school_year, meeting_date — so we can answer the 7
sample queries against real data.

Run:
    poetry run python scripts/qdrant_inspect_tenant2.py
"""

from __future__ import annotations

import json
from collections import Counter
from typing import Any

import httpx

COLLECTION = "justedtech_2_documents"
URL = "http://localhost:6343"


def scroll_all() -> list[dict[str, Any]]:
    points: list[dict[str, Any]] = []
    offset: str | None = None
    while True:
        body: dict[str, Any] = {
            "limit": 1000,
            "with_payload": True,
            "with_vector": False,
        }
        if offset is not None:
            body["offset"] = offset
        resp = httpx.post(
            f"{URL}/collections/{COLLECTION}/points/scroll",
            json=body,
            timeout=60.0,
        )
        resp.raise_for_status()
        data = resp.json()["result"]
        batch = data.get("points") or []
        points.extend(batch)
        next_offset = data.get("next_page_offset")
        if next_offset is None or not batch:
            break
        offset = next_offset
    return points


def main() -> None:
    points = scroll_all()
    print(f"Total points: {len(points)}")

    classified = sum(1 for p in points if p.get("payload", {}).get("classified"))
    with_tags = sum(1 for p in points if p.get("payload", {}).get("topic_tags"))
    with_topics = sum(1 for p in points if p.get("payload", {}).get("topics"))
    with_action_types = sum(
        1 for p in points if p.get("payload", {}).get("action_types")
    )
    with_action_stage = sum(
        1 for p in points if p.get("payload", {}).get("action_stage")
    )
    with_meeting_date = sum(
        1 for p in points if p.get("payload", {}).get("meeting_date")
    )

    print(f"classified:              {classified}")
    print(f"with topic_tags:         {with_tags}")
    print(f"with topics:            {with_topics}")
    print(f"with action_types:      {with_action_types}")
    print(f"with action_stage:      {with_action_stage}")
    print(f"with meeting_date:      {with_meeting_date}")
    print()

    tag_pair: Counter[tuple[str, str]] = Counter()
    tag_cat: Counter[str] = Counter()
    topics_counter: Counter[str] = Counter()
    action_types_counter: Counter[str] = Counter()
    action_stage_counter: Counter[str] = Counter()
    mdoc_counter: Counter[str] = Counter()
    year_counter: Counter[str] = Counter()
    qm_counter: Counter[str] = Counter()
    district_counter: Counter[str] = Counter()
    state_counter: Counter[str] = Counter()
    md_year: Counter[str] = Counter()

    for p in points:
        pl = p.get("payload", {})
        for t in pl.get("topic_tags") or []:
            if isinstance(t, dict):
                tag_pair[(t.get("category"), t.get("subtopic"))] += 1
                tag_cat[t.get("category")] += 1
        for t in pl.get("topics") or []:
            topics_counter[t] += 1
        for a in pl.get("action_types") or []:
            action_types_counter[a] += 1
        if pl.get("action_stage"):
            action_stage_counter[pl["action_stage"]] += 1
        if pl.get("meeting_doc_type"):
            mdoc_counter[pl["meeting_doc_type"]] += 1
        if pl.get("school_year"):
            year_counter[pl["school_year"]] += 1
        if pl.get("quarter_month"):
            qm_counter[pl["quarter_month"]] += 1
        if pl.get("district_name"):
            district_counter[pl["district_name"]] += 1
        if pl.get("state"):
            state_counter[pl["state"]] += 1
        if pl.get("meeting_date"):
            md_year[pl["meeting_date"][:7]] += 1

    print("--- topic_tags category ---")
    for k, v in tag_cat.most_common():
        print(f"  {k}: {v}")
    print("--- topic_tags (category, subtopic) ---")
    for k, v in tag_pair.most_common():
        print(f"  {k}: {v}")
    print("--- coarse topics ---")
    for k, v in topics_counter.most_common():
        print(f"  {k}: {v}")
    print("--- action_types ---")
    for k, v in action_types_counter.most_common():
        print(f"  {k}: {v}")
    print("--- action_stage ---")
    for k, v in action_stage_counter.most_common():
        print(f"  {k}: {v}")
    print("--- meeting_doc_type ---")
    for k, v in mdoc_counter.most_common():
        print(f"  {k}: {v}")
    print("--- school_year ---")
    for k, v in year_counter.most_common():
        print(f"  {k}: {v}")
    print("--- meeting_date (YYYY-MM) top 20 ---")
    for k, v in md_year.most_common(20):
        print(f"  {k}: {v}")
    print("--- top 20 districts ---")
    for k, v in district_counter.most_common(20):
        print(f"  {k}: {v}")
    print("--- state ---")
    for k, v in state_counter.most_common():
        print(f"  {k}: {v}")

    # Save a small JSON summary so we can build the golden dataset
    summary = {
        "total_points": len(points),
        "classified": classified,
        "with_topic_tags": with_tags,
        "with_topics": with_topics,
        "topic_tags_category": dict(tag_cat),
        "topic_tags_pair": [list(k) + [v] for k, v in tag_pair.most_common()],
        "coarse_topics": dict(topics_counter),
        "action_types": dict(action_types_counter),
        "action_stage": dict(action_stage_counter),
        "meeting_doc_type": dict(mdoc_counter),
        "school_year": dict(year_counter),
        "districts_top20": dict(district_counter.most_common(20)),
    }
    with open("/tmp/qdrant_tenant2_summary.json", "w") as f:
        json.dump(summary, f, indent=2)
    print("\nWrote /tmp/qdrant_tenant2_summary.json")


if __name__ == "__main__":
    main()
