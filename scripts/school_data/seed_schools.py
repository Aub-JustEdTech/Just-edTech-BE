#!/usr/bin/env python3
"""
Seed the `schools` table from scripts/school_data/output/school_names.json.

Loads the 396 MA school districts into the schools table, scoped to a
single tenant (defaults to settings.DEFAULT_TENANT_ID). Idempotent on
(tenant_id, org_code): existing rows are left untouched, missing rows
are inserted, and disabled schools can be deactivated via --deactivate-missing.

Usage:
    python scripts/school_data/seed_schools.py
    python scripts/school_data/seed_schools.py --tenant-id 1
    python scripts/school_data/seed_schools.py --json path/to/school_names.json
    python scripts/school_data/seed_schools.py --deactivate-missing
"""

import argparse
import asyncio
import json
import sys
from pathlib import Path

from sqlalchemy import select

from app.core.config import settings
from app.db.connector import AsyncSessionLocal
from app.models.school import School

DEFAULT_JSON_PATH = (
    Path(__file__).parent / "output" / "school_names.json"
)


async def seed_schools(
    tenant_id: int,
    json_path: Path,
    deactivate_missing: bool = False,
) -> dict:
    print("=" * 60)
    print("Just-EdTech School Seeder")
    print(f"  tenant_id          : {tenant_id}")
    print(f"  source             : {json_path}")
    print(f"  deactivate_missing : {deactivate_missing}")
    print("=" * 60)

    if not json_path.exists():
        raise FileNotFoundError(f"School names JSON not found: {json_path}")

    with json_path.open("r", encoding="utf-8") as f:
        records = json.load(f)

    print(f"Loaded {len(records)} school records from JSON.")

    stats = {"inserted": 0, "existing": 0, "deactivated": 0, "skipped_no_org": 0}
    seen_org_codes: set[str] = set()

    async with AsyncSessionLocal() as db:
        for rec in records:
            org_code = (rec.get("org_code") or "").strip()
            name = (rec.get("name") or "").strip()
            if not org_code:
                stats["skipped_no_org"] += 1
                continue

            seen_org_codes.add(org_code)

            existing = (
                await db.execute(
                    select(School).where(
                        School.tenant_id == tenant_id,
                        School.org_code == org_code,
                    )
                )
            ).scalar_one_or_none()

            if existing:
                # Update mutable fields only; do not clobber last_scrapped_at.
                existing.name = name
                existing.district_type = rec.get("district_type") or existing.district_type
                existing.website = rec.get("website") or existing.website
                stats["existing"] += 1
                continue

            school = School(
                tenant_id=tenant_id,
                org_code=org_code,
                name=name,
                district_type=rec.get("district_type") or "Public School District",
                website=rec.get("website"),
                is_active=True,
            )
            db.add(school)
            stats["inserted"] += 1

        if deactivate_missing:
            all_rows = (
                await db.execute(
                    select(School).where(School.tenant_id == tenant_id)
                )
            ).scalars().all()
            for row in all_rows:
                if row.org_code not in seen_org_codes:
                    row.is_active = False
                    stats["deactivated"] += 1

        await db.commit()

    print("\nSeed results:")
    for k, v in stats.items():
        print(f"  {k:<20}: {v}")
    print("\nDone.")
    return stats


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed schools table.")
    parser.add_argument(
        "--tenant-id",
        type=int,
        default=int(getattr(settings, "DEFAULT_TENANT_ID", 1) or 1),
        help="Tenant ID to scope the seed (default: settings.DEFAULT_TENANT_ID).",
    )
    parser.add_argument(
        "--json",
        type=Path,
        default=DEFAULT_JSON_PATH,
        help="Path to school_names.json.",
    )
    parser.add_argument(
        "--deactivate-missing",
        action="store_true",
        help="Deactivate schools present in DB but missing from JSON.",
    )
    args = parser.parse_args()

    try:
        asyncio.run(
            seed_schools(
                tenant_id=args.tenant_id,
                json_path=args.json,
                deactivate_missing=args.deactivate_missing,
            )
        )
    except Exception as exc:
        print(f"\nSeeding failed: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
