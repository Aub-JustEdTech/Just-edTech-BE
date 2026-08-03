#!/usr/bin/env python3
"""
Set the primary SchoolScrapeUrl for each district in
scripts/school_data/output/finalised_20_disticts.json using the
manually reviewed `correct_URL` field.

For each school:
  - Look up by org_code.
  - Upsert the correct_URL via app.crud.schools.add_scrape_url and set it
    as the school's primary scrape URL (overwrites any previous primary).

Idempotent: safe to re-run.

Usage:
    python scripts/school_data/feed_finalised_scrape_urls.py
    python scripts/school_data/feed_finalised_scrape_urls.py --dry-run
    python scripts/school_data/feed_finalised_scrape_urls.py \\
        --json path/to/finalised_20_disticts.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from sqlalchemy import select

from app.crud import schools as crud
from app.db.connector import AsyncSessionLocal
from app.models.school import School, SchoolScrapeUrl
from app.schemas.schools import ScrapeUrlCreate

DEFAULT_JSON_PATH = (
    Path(__file__).parent / "output" / "finalised_20_disticts.json"
)


def _extract_correct_url(rec: dict) -> str | None:
    """Return trimmed correct_URL, tolerating the Wachusett key typo."""
    for key in ("correct_URL", "correct _URL"):
        raw = rec.get(key)
        if raw and str(raw).strip():
            return str(raw).strip()
    return None


async def feed_finalised_scrape_urls(json_path: Path, dry_run: bool) -> dict:
    print("=" * 60)
    print("Just-EdTech Finalised Scrape-URL Feeder")
    print(f"  source   : {json_path}")
    print(f"  dry_run  : {dry_run}")
    print("=" * 60)

    if not json_path.exists():
        raise FileNotFoundError(f"JSON not found: {json_path}")

    with json_path.open("r", encoding="utf-8") as f:
        schools_data = json.load(f)

    stats = {
        "total": len(schools_data),
        "confirmed": 0,
        "unchanged": 0,
        "no_url": 0,
        "school_not_found": 0,
    }
    decisions: list[dict] = []

    async with AsyncSessionLocal() as db:
        for rec in schools_data:
            org_code = rec.get("org_code", "").strip()
            name = rec.get("name", "").strip()
            url = _extract_correct_url(rec)

            if not url:
                print(f"  [skip] {name} ({org_code}) — missing correct_URL")
                stats["no_url"] += 1
                decisions.append(
                    {"org_code": org_code, "name": name, "action": "no_url"}
                )
                continue

            school = (
                await db.execute(
                    select(School).where(School.org_code == org_code)
                )
            ).scalar_one_or_none()

            if not school:
                print(f"  [skip] {name} ({org_code}) — school not in DB")
                stats["school_not_found"] += 1
                decisions.append(
                    {"org_code": org_code, "name": name, "action": "school_not_found"}
                )
                continue

            existing_url: str | None = None
            if school.scrape_url_id is not None:
                existing = await db.get(SchoolScrapeUrl, school.scrape_url_id)
                existing_url = existing.url if existing else None

            if existing_url == url:
                print(f"  [ok]   {name} ({org_code}) — already set: {url}")
                stats["unchanged"] += 1
                decisions.append(
                    {
                        "org_code": org_code,
                        "name": name,
                        "action": "unchanged",
                        "url": url,
                    }
                )
                continue

            if existing_url:
                print(
                    f"  [set]  {name} ({org_code}) — "
                    f"replacing {existing_url}"
                )
            else:
                print(f"  [set]  {name} ({org_code})")
            print(f"          url: {url}")

            if dry_run:
                decisions.append(
                    {
                        "org_code": org_code,
                        "name": name,
                        "action": "would_confirm",
                        "url": url,
                        "previous_url": existing_url,
                    }
                )
                continue

            scrape_url = await crud.add_scrape_url(
                db,
                school,
                ScrapeUrlCreate(
                    url=url,
                    crawl_depth=2,
                    use_playwright=True,
                    is_primary=True,
                ),
                user_id=None,
            )
            stats["confirmed"] += 1
            decisions.append(
                {
                    "org_code": org_code,
                    "name": name,
                    "action": "confirmed",
                    "url": url,
                    "previous_url": existing_url,
                    "scrape_url_id": scrape_url.id,
                }
            )

    print("\nFeed results:")
    for k, v in stats.items():
        print(f"  {k:<20}: {v}")
    print("\nDone.")
    return {"stats": stats, "decisions": decisions}


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Feed manually finalised scrape URLs into the schools table."
    )
    parser.add_argument(
        "--json",
        type=Path,
        default=DEFAULT_JSON_PATH,
        help="Path to finalised_20_disticts.json.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print decisions without writing to the DB.",
    )
    args = parser.parse_args()

    try:
        asyncio.run(feed_finalised_scrape_urls(args.json, args.dry_run))
    except Exception as exc:
        print(f"\nFeed failed: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
