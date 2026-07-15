#!/usr/bin/env python3
"""
Seed stored URL-discovery results from school_url_candidates.json.

Reads scripts/school_data/output/school_url_candidates.json (produced by
discover_school_candidates.py) and upserts rows into school_url_discoveries
and school_url_candidates for each matching school in the tenant.

Idempotent per school: re-running replaces the stored candidate list.

Usage:
    python scripts/school_data/seed_url_candidates.py
    python scripts/school_data/seed_url_candidates.py --tenant-id 1
    python scripts/school_data/seed_url_candidates.py \\
        --json scripts/school_data/output/school_url_candidates.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from sqlalchemy import select

from app.core.config import settings
from app.crud import school_url_discovery as discovery_crud
from app.db.connector import AsyncSessionLocal
from app.models.school import School

DEFAULT_JSON_PATH = (
    Path(__file__).parent / "output" / "school_url_candidates.json"
)


async def seed_url_candidates(
    tenant_id: int,
    json_path: Path,
    max_candidates: int,
) -> dict[str, int]:
    if not json_path.exists():
        raise FileNotFoundError(f"Candidates JSON not found: {json_path}")

    with json_path.open("r", encoding="utf-8") as handle:
        records = json.load(handle)

    stats = {
        "processed": 0,
        "seeded": 0,
        "missing_school": 0,
        "skipped_no_org": 0,
    }

    async with AsyncSessionLocal() as db:
        for record in records:
            stats["processed"] += 1
            org_code = (record.get("org_code") or "").strip()
            if not org_code:
                stats["skipped_no_org"] += 1
                continue

            school = (
                await db.execute(
                    select(School).where(
                        School.tenant_id == tenant_id,
                        School.org_code == org_code,
                    )
                )
            ).scalar_one_or_none()
            if school is None:
                stats["missing_school"] += 1
                print(f"  WARN: no school row for org_code={org_code}", file=sys.stderr)
                continue

            await discovery_crud.replace_discovery_for_school(
                db,
                school,
                discovery_method=record.get("discovery_method"),
                total_urls_scanned=int(record.get("total_urls_scanned") or 0),
                error=record.get("error"),
                raw_candidates=list(record.get("candidates") or []),
                max_candidates=max_candidates,
            )
            stats["seeded"] += 1
            print(f"  seeded: {school.name} ({org_code})")

    return stats


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Seed school URL discovery candidates into the database."
    )
    parser.add_argument(
        "--tenant-id",
        type=int,
        default=settings.DEFAULT_TENANT_ID,
        help="Tenant that owns the school rows.",
    )
    parser.add_argument(
        "--json",
        type=Path,
        default=DEFAULT_JSON_PATH,
        help="Path to school_url_candidates.json.",
    )
    parser.add_argument(
        "--max-candidates",
        type=int,
        default=settings.SCHOOL_SCRAPER_MAX_CANDIDATES,
        help="Store at most this many deduplicated candidates per school.",
    )
    args = parser.parse_args()

    print("=" * 60)
    print("Just-EdTech URL Candidate Seeder")
    print(f"  tenant_id       : {args.tenant_id}")
    print(f"  source          : {args.json}")
    print(f"  max_candidates  : {args.max_candidates}")
    print("=" * 60)

    stats = asyncio.run(
        seed_url_candidates(
            tenant_id=args.tenant_id,
            json_path=args.json,
            max_candidates=args.max_candidates,
        )
    )

    print("=" * 60)
    print("Done.")
    for key, value in stats.items():
        print(f"  {key}: {value}")
    print("=" * 60)


if __name__ == "__main__":
    main()
