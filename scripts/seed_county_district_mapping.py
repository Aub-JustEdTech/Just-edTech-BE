"""
Seed county_district_mapping from NCES CCD CSV.

Usage:
    python scripts/seed_county_district_mapping.py --csv path/to/ccd_districts.csv

Expected CSV columns (NCES CCD Public School District Universe):
    NAME    — district name
    CONAME  — county name
    STABR   — state abbreviation (filter to "MA")
    CONUM   — county FIPS code (optional)

Download from: https://nces.ed.gov/ccd/ccddata.asp
"""

import argparse
import asyncio
import csv
import logging
import sys
from pathlib import Path

# Allow running from project root
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from datetime import datetime

from sqlalchemy import select

from app.db.connector import AsyncSessionLocal
from app.models.heatmap import CountyDistrictMapping

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def seed(csv_path: str, dry_run: bool = False) -> None:
    rows_to_insert: list[dict] = []

    with open(csv_path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            stabr = row.get("STABR", row.get("stabr", "")).strip()
            if stabr.upper() != "MA":
                continue

            district_name = (row.get("NAME") or row.get("name") or "").strip()
            county_name = (row.get("CONAME") or row.get("coname") or "").strip()
            fips_code = (row.get("CONUM") or row.get("conum") or "").strip() or None

            if not district_name or not county_name:
                continue

            rows_to_insert.append(
                {
                    "district_name": district_name,
                    "county_name": county_name,
                    "state": "MA",
                    "fips_code": fips_code,
                }
            )

    logger.info(f"Found {len(rows_to_insert)} MA districts in CSV")

    if dry_run:
        for r in rows_to_insert[:5]:
            logger.info(f"  DRY RUN: {r}")
        return

    async with AsyncSessionLocal() as db:
        existing = await db.execute(select(CountyDistrictMapping.district_name).where(CountyDistrictMapping.state == "MA"))
        existing_names = {row[0] for row in existing.fetchall()}

        inserted = 0
        now = datetime.utcnow()
        for row in rows_to_insert:
            if row["district_name"] in existing_names:
                continue
            db.add(
                CountyDistrictMapping(
                    district_name=row["district_name"],
                    county_name=row["county_name"],
                    state=row["state"],
                    fips_code=row["fips_code"],
                    created_at=now,
                    updated_at=now,
                )
            )
            inserted += 1

        await db.commit()
        logger.info(f"Inserted {inserted} new rows (skipped {len(rows_to_insert) - inserted} duplicates)")


def main():
    parser = argparse.ArgumentParser(description="Seed county_district_mapping from NCES CCD CSV")
    parser.add_argument("--csv", required=True, help="Path to NCES CCD CSV file")
    parser.add_argument("--dry-run", action="store_true", help="Parse CSV and log without inserting")
    args = parser.parse_args()

    asyncio.run(seed(args.csv, dry_run=args.dry_run))


if __name__ == "__main__":
    main()
