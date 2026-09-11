#!/usr/bin/env python3
"""
Feed one or more finalised SchoolScrapeUrl rows per district.

Supports two JSON shapes:

  1. Multi-URL (preferred for historical ingest):

     [
       {
         "org_code": "07530000",
         "name": "Quabbin",
         "urls": [
           "https://www.qrsd.org/meeting-minutes",
           "https://www.qrsd.org/agendas"
         ]
       },
       {
         "org_code": "...",
         "name": "...",
         "urls": [
           {
             "url": "https://www.example.org/boarddocs",
             "crawl_depth": 3,
             "use_playwright": true
           }
         ]
       }
     ]

  2. Legacy single-URL (backward compatible):

     [
       {
         "org_code": "07530000",
         "name": "Quabbin",
         "correct_URL": "https://www.qrsd.org/meeting-minutes"
       }
     ]

For each school:
  - Look up by org_code.
  - Upsert every URL via app.crud.schools.add_scrape_url (idempotent on
    (school_id, url)).
  - Deactivate any active DB URL for this school that is NOT in the new
    list (only when --prune is passed) so stale confirmed URLs from a
    prior review cycle don't keep getting scraped.

Idempotent: safe to re-run. Existing matching URLs are updated in place.

Usage:
    python scripts/school_data/feed_finalised_scrape_urls.py
    python scripts/school_data/feed_finalised_scrape_urls.py --dry-run
    python scripts/school_data/feed_finalised_scrape_urls.py \\
        --json path/to/finalised_urls.json
    python scripts/school_data/feed_finalised_scrape_urls.py --prune
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.crud import schools as crud
from app.db.connector import AsyncSessionLocal
from app.models.school import School, SchoolScrapeUrl
from app.schemas.schools import ScrapeUrlCreate

DEFAULT_JSON_PATH = (
    Path(__file__).parent / "output" / "finalised_20_disticts.json"
)

DEFAULT_CRAWL_DEPTH = 2
DEFAULT_USE_PLAYWRIGHT = True


def _extract_urls(rec: dict) -> list[dict]:
    """Return a list of URL config dicts for one school record.

    Each dict has keys: url, crawl_depth, use_playwright.
    Accepts both the new ``urls`` field and the legacy ``correct_URL``
    field (mutually transparent — ``urls`` wins when both are present).
    """
    raw_urls = rec.get("urls")
    if raw_urls and isinstance(raw_urls, list):
        out: list[dict] = []
        for entry in raw_urls:
            if isinstance(entry, str):
                url = entry.strip()
                if not url:
                    continue
                out.append(
                    {
                        "url": url,
                        "crawl_depth": DEFAULT_CRAWL_DEPTH,
                        "use_playwright": DEFAULT_USE_PLAYWRIGHT,
                    }
                )
            elif isinstance(entry, dict):
                url = str(entry.get("url") or "").strip()
                if not url:
                    continue
                out.append(
                    {
                        "url": url,
                        "crawl_depth": int(
                            entry.get("crawl_depth", DEFAULT_CRAWL_DEPTH)
                        ),
                        "use_playwright": bool(
                            entry.get("use_playwright", DEFAULT_USE_PLAYWRIGHT)
                        ),
                    }
                )
        return out

    # Legacy single-URL shape.
    for key in ("correct_URL", "correct _URL"):
        raw = rec.get(key)
        if raw and str(raw).strip():
            return [
                {
                    "url": str(raw).strip(),
                    "crawl_depth": DEFAULT_CRAWL_DEPTH,
                    "use_playwright": DEFAULT_USE_PLAYWRIGHT,
                }
            ]
    return []


async def feed_finalised_scrape_urls(
    json_path: Path, dry_run: bool, prune: bool
) -> dict:
    print("=" * 60)
    print("Just-EdTech Finalised Scrape-URL Feeder (multi-URL)")
    print(f"  source   : {json_path}")
    print(f"  dry_run  : {dry_run}")
    print(f"  prune    : {prune}")
    print("=" * 60)

    if not json_path.exists():
        raise FileNotFoundError(f"JSON not found: {json_path}")

    with json_path.open("r", encoding="utf-8") as f:
        schools_data = json.load(f)

    stats = {
        "total_schools": len(schools_data),
        "schools_with_urls": 0,
        "urls_upserted": 0,
        "urls_unchanged": 0,
        "urls_pruned": 0,
        "no_url": 0,
        "school_not_found": 0,
    }
    decisions: list[dict] = []

    async with AsyncSessionLocal() as db:
        for rec in schools_data:
            org_code = rec.get("org_code", "").strip()
            name = rec.get("name", "").strip()
            url_configs = _extract_urls(rec)

            if not url_configs:
                print(f"  [skip] {name} ({org_code}) — no URLs")
                stats["no_url"] += 1
                decisions.append(
                    {"org_code": org_code, "name": name, "action": "no_url"}
                )
                continue

            school = (
                await db.execute(
                    select(School)
                    .where(School.org_code == org_code)
                    .options(selectinload(School.scrape_urls))
                )
            ).scalar_one_or_none()

            if not school:
                print(f"  [skip] {name} ({org_code}) — school not in DB")
                stats["school_not_found"] += 1
                decisions.append(
                    {
                        "org_code": org_code,
                        "name": name,
                        "action": "school_not_found",
                    }
                )
                continue

            stats["schools_with_urls"] += 1
            incoming_urls = {c["url"] for c in url_configs}
            school_decisions: list[dict] = []

            for config in url_configs:
                existing_url_row: SchoolScrapeUrl | None = None
                for su in school.scrape_urls or []:
                    if su.url == config["url"]:
                        existing_url_row = su
                        break

                if existing_url_row and existing_url_row.is_active:
                    if (
                        existing_url_row.crawl_depth == config["crawl_depth"]
                        and existing_url_row.use_playwright
                        == config["use_playwright"]
                    ):
                        print(
                            f"  [ok]   {name} ({org_code}) — "
                            f"already set: {config['url']}"
                        )
                        stats["urls_unchanged"] += 1
                        school_decisions.append(
                            {
                                "url": config["url"],
                                "action": "unchanged",
                                "scrape_url_id": existing_url_row.id,
                            }
                        )
                        continue

                if dry_run:
                    print(
                        f"  [dry]  {name} ({org_code}) — "
                        f"{'would upsert' if not existing_url_row else 'would update'}: "
                        f"{config['url']}"
                    )
                    school_decisions.append(
                        {
                            "url": config["url"],
                            "action": "would_upsert",
                        }
                    )
                    continue

                scrape_url = await crud.add_scrape_url(
                    db,
                    school,
                    ScrapeUrlCreate(
                        url=config["url"],
                        crawl_depth=config["crawl_depth"],
                        use_playwright=config["use_playwright"],
                    ),
                    user_id=None,
                )
                stats["urls_upserted"] += 1
                print(
                    f"  [set]  {name} ({org_code}) — "
                    f"{config['url']} (id={scrape_url.id})"
                )
                school_decisions.append(
                    {
                        "url": config["url"],
                        "action": "upserted",
                        "scrape_url_id": scrape_url.id,
                    }
                )

            # Prune stale active URLs not in the incoming list.
            if prune and not dry_run:
                for su in school.scrape_urls or []:
                    if su.is_active and su.url not in incoming_urls:
                        su.is_active = False
                        stats["urls_pruned"] += 1
                        print(
                            f"  [prune] {name} ({org_code}) — "
                            f"deactivated stale: {su.url}"
                        )
                        school_decisions.append(
                            {
                                "url": su.url,
                                "action": "pruned",
                                "scrape_url_id": su.id,
                            }
                        )

            if not dry_run:
                await db.commit()

            decisions.append(
                {
                    "org_code": org_code,
                    "name": name,
                    "action": "processed",
                    "urls": school_decisions,
                }
            )

    print("\nFeed results:")
    for k, v in stats.items():
        print(f"  {k:<20}: {v}")
    print("\nDone.")
    return {"stats": stats, "decisions": decisions}


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Feed finalised scrape URLs (one or more) into the schools table."
    )
    parser.add_argument(
        "--json",
        type=Path,
        default=DEFAULT_JSON_PATH,
        help="Path to finalised URLs JSON.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print decisions without writing to the DB.",
    )
    parser.add_argument(
        "--prune",
        action="store_true",
        help=(
            "Deactivate any active DB scrape URL for a school that is not "
            "in the incoming JSON. Use when the JSON is the complete, "
            "authoritative URL set per school."
        ),
    )
    args = parser.parse_args()

    try:
        asyncio.run(
            feed_finalised_scrape_urls(args.json, args.dry_run, args.prune)
        )
    except Exception as exc:
        print(f"\nFeed failed: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
