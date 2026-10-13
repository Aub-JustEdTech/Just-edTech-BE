#!/usr/bin/env python3
"""
Fix known bad/outdated seed websites in prod (manual Layer-1 unlocks).

Currently:
  - Rowe (org_code from school_names) — Roweschool.com does not resolve.
    Set the correct district website once known.

Usage (prod, after confirming the correct URL):
    docker exec just-edtech-api python \\
      scripts/school_data/fix_seed_websites.py \\
      --tenant-id 4 \\
      --org-code <rowe_org_code> \\
      --website https://correct-site.example \\
      --dry-run
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from sqlalchemy import select

from app.db.connector import AsyncSessionLocal
from app.models.school import School


async def fix_website(
    *, tenant_id: int, org_code: str, website: str, dry_run: bool
) -> int:
    async with AsyncSessionLocal() as db:
        school = (
            await db.execute(
                select(School).where(
                    School.tenant_id == tenant_id, School.org_code == org_code
                )
            )
        ).scalar_one_or_none()
        if school is None:
            print(f"school_not_found: tenant={tenant_id} org_code={org_code}")
            return 1
        print(f"{school.name} ({org_code})")
        print(f"  current: {school.website}")
        print(f"  new    : {website}")
        if dry_run:
            print("  [dry-run] no write")
            return 0
        school.website = website
        await db.commit()
        print("  [set] updated")
        return 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tenant-id", type=int, required=True)
    parser.add_argument("--org-code", type=str, required=True)
    parser.add_argument("--website", type=str, required=True)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    try:
        code = asyncio.run(
            fix_website(
                tenant_id=args.tenant_id,
                org_code=args.org_code.strip(),
                website=args.website.strip(),
                dry_run=args.dry_run,
            )
        )
    except Exception as exc:  # noqa: BLE001
        print(f"failed: {exc}", file=sys.stderr)
        sys.exit(1)
    sys.exit(code)


if __name__ == "__main__":
    main()
