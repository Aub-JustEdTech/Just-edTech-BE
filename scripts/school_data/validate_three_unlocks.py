#!/usr/bin/env python3
"""Quick live validation of the three crawler unlocks.

Runs the real SchemaDrivenCrawler (LLM + network) against a small sample
picked from the failure-batch results, one per unlock:

  - Acton-Boxborough  http://www.abschools.org        -> ERR_HTTP2_PROTOCOL_ERROR (unlock #1)
  - Fitchburg         http://www.fitchburgschools.org -> hub-1-page, 0 data pages  (unlock #2)
  - Wareham-style redirect case is covered by unit tests; a same-org
    subdomain case is picked from the failure batch if present.

Prints before/after pages_crawled, llm_calls, data_pages, errors for each,
plus the FetchMeta stages_tried so we can see playwright_http1 fire.

Usage:
    poetry run python scripts/school_data/validate_three_unlocks.py
"""
from __future__ import annotations

import asyncio
import json
import sys
from datetime import date
from pathlib import Path

from app.services.web_scraper.schema_driven_crawler import SchemaDrivenCrawler

PRIOR_RESULTS = Path(__file__).resolve().parents[1] / (
    "output/schema_crawl_failure_rerun_2026-09-08/schema_crawl_results.json"
)

SAMPLE_SCHOOLS = [
    # Hub-1-page + cross-domain redirect: redirects to sites.google.com
    ("Fitchburg", "http://www.fitchburgschools.org", "hub-1-page + cross-domain redirect"),
    # Hub with 15 pages but 0 data — harvest should find meeting links
    ("Newburyport", "https://www.newburyport.k12.ma.us", "hub, 0 data pages"),
    # Another hub case
    ("Pentucket", "https://www.prsd.org", "hub, 0 data pages"),
]


def _prior_for(name: str) -> dict | None:
    if not PRIOR_RESULTS.exists():
        return None
    for r in json.load(open(PRIOR_RESULTS)):
        if r.get("name") == name:
            return r
    return None


async def crawl_one(name: str, website: str, label: str) -> None:
    prior = _prior_for(name)
    print(f"\n{'=' * 70}")
    print(f"{name}  |  {website}")
    print(f"unlock targeted: {label}")
    if prior:
        print(
            f"BEFORE: pages_crawled={prior.get('pages_crawled')} "
            f"llm_calls={prior.get('llm_calls')} "
            f"data_pages={len(prior.get('data_pages') or [])} "
            f"errors={len(prior.get('errors') or [])}"
        )
        if prior.get("errors"):
            print(f"  prior first error: {prior['errors'][0][:140]}")
    else:
        print("BEFORE: (no prior result found in failure rerun)")

    crawler = SchemaDrivenCrawler(
        max_pages=20, skip_archival=False, confidence_threshold=0.5
    )
    try:
        result = await crawler.crawl(website)
        print(
            f"AFTER : pages_crawled={result.pages_crawled} "
            f"llm_calls={result.llm_calls} "
            f"data_pages={len(result.data_pages)} "
            f"errors={len(result.error_details)} "
            f"max_pages_limit_reached={result.max_pages_limit_reached}"
        )
        if result.data_pages:
            for dp in result.data_pages[:3]:
                info = dp.data_page_info
                print(
                    f"  DATA PAGE: {dp.url} "
                    f"(type={info.data_type if info else '?'}, "
                    f"archive={info.is_archive if info else '?'})"
                )
        if result.error_details:
            for e in result.error_details[:3]:
                print(
                    f"  ERROR: {e.code} {e.url} "
                    f"status={e.http_status} exc={e.exception_type} "
                    f"stage={e.stage}"
                )
        visited_hubs = [
            p for p in result.visited_pages if p.has_data_links and not p.has_data
        ]
        if visited_hubs:
            print(f"  visited hubs (has_data_links=True, has_data=False): {len(visited_hubs)}")
            for h in visited_hubs[:3]:
                print(f"    hub: {h.url}")
    finally:
        await crawler.close()


async def main() -> int:
    print("Validating three crawler unlocks against live school sites...")
    print(f"today: {date.today()}")
    for name, website, label in SAMPLE_SCHOOLS:
        try:
            await crawl_one(name, website, label)
        except Exception as exc:  # noqa: BLE001
            print(f"  !! crawl raised {type(exc).__name__}: {exc}")
    print(f"\n{'=' * 70}\nDone.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
