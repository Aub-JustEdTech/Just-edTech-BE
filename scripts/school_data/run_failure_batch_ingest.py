#!/usr/bin/env python3
"""
One-command trigger for the failure-batch re-scrape on production.

This is the single entry point for the 84 failure-batch schools. It chains
the two existing scripts so you don't have to remember their flags:

  1. feed_finalised_scrape_urls — upserts the dry-run-discovered data-page
     URLs into the `school_scrape_urls` table (idempotent on (school_id, url)).
  2. run_scrape_districts — scoped to the failure-batch org_codes, scrapes
     each confirmed URL, persists ScrapedMedia rows, and enqueues
     `ingest_scraped_media` Celery tasks onto the `scraping` queue.

The Celery workers (`celery-scraper` + `celery-scraper-batch` on the
`scraping` queue; `celery-ingest` + `celery-ingest-batch` on the `documents`
queue) pick up the tasks and run the document pipeline.

Run this inside the `api` container on the EC2 host AFTER the batch worker
replicas are up:

    docker exec just-edtech-api python \\
        scripts/school_data/run_failure_batch_ingest.py

Modes:
    --dry-run        Seed nothing, scrape nothing — print what would happen.
    --seed-only      Feed scrape URLs into the DB but do not trigger the scrape.
    --scrape-only    Skip seeding (assume URLs already seeded) and just trigger
                     the scrape for the failure-batch org_codes.

The default (no flag) runs both seed + scrape.

Usage:
    # Full run (seed URLs + trigger scrape + enqueue ingest):
    docker exec just-edtech-api python \\
        scripts/school_data/run_failure_batch_ingest.py

    # Dry run first to see what would happen:
    docker exec just-edtech-api python \\
        scripts/school_data/run_failure_batch_ingest.py --dry-run

    # Seed only (review URLs before scraping):
    docker exec just-edtech-api python \\
        scripts/school_data/run_failure_batch_ingest.py --seed-only

    # Scrape only (URLs already seeded in a prior --seed-only run):
    docker exec just-edtech-api python \\
        scripts/school_data/run_failure_batch_ingest.py --scrape-only

Prerequisites on the EC2 host:
    1. git pull (so the latest scripts + app/ are live in the container
       via the bind mount).
    2. Batch workers up:
         docker compose -f docker-compose.prod.yml up -d \\
           --scale celery-scraper=2 --scale celery-scraper-batch=2 \\
           --scale celery-ingest=2 --scale celery-ingest-batch=2
    3. Then run this script via docker exec (see above).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

DEFAULT_SCRAPE_URLS_JSON = (
    Path(__file__).resolve().parents[2]
    / "scripts/school_data/output/schema_crawl_failure_rerun_2026-09-14/"
    "finalised_scrape_urls.json"
)


async def _load_org_codes(json_path: Path) -> list[str]:
    """Read the finalised scrape URLs JSON and return the org_code list."""
    if not json_path.exists():
        print(f"ERROR: scrape URLs JSON not found: {json_path}", file=sys.stderr)
        sys.exit(1)
    records = json.load(open(json_path))
    codes = [str(r.get("org_code", "")).strip() for r in records if r.get("org_code")]
    return [c for c in codes if c]


async def _seed_scrape_urls(json_path: Path, dry_run: bool) -> None:
    """Feed finalised scrape URLs into school_scrape_urls.

    Delegates to the existing feed_finalised_scrape_urls.async function so
    the upsert + prune logic stays in one place.
    """
    from scripts.school_data.feed_finalised_scrape_urls import (
        feed_finalised_scrape_urls,
    )

    print("\n" + "=" * 70)
    print("[1/2] Seeding scrape URLs")
    print("=" * 70)
    await feed_finalised_scrape_urls(json_path, dry_run=dry_run, prune=False)


async def _trigger_scrape(
    json_path: Path, dry_run: bool, tenant_id: int, concurrency: int
) -> None:
    """Trigger run_scrape_districts scoped to the failure-batch org_codes.

    Delegates to the existing run_scrape_districts.run_scrape_districts so
    the scrape + persist + enqueue logic stays in one place.
    """
    from scripts.school_data.run_scrape_districts import run_scrape_districts

    org_codes = await _load_org_codes(json_path)

    print("\n" + "=" * 70)
    print("[2/2] Triggering scrape + enqueue ingest")
    print("=" * 70)
    print(f"  org_codes: {len(org_codes)} schools")
    print(f"  tenant_id: {tenant_id}")
    print(f"  concurrency: {concurrency}")

    await run_scrape_districts(
        tenant_id=tenant_id,
        json_path=None,
        org_codes_arg=org_codes,
        crawl_depth=2,
        use_playwright=None,
        concurrency=concurrency,
        dry_run=dry_run,
        enqueue=True,
        documents_only=False,
        skip_scraped=False,
        revisit_max_docs=None,
    )


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--json",
        type=Path,
        default=DEFAULT_SCRAPE_URLS_JSON,
        help="Finalised scrape URLs JSON (default: 2026-09-14 dry-run output)",
    )
    parser.add_argument(
        "--tenant-id",
        type=int,
        default=1,
        help="Tenant ID (default: 1 = DEFAULT_TENANT_ID)",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=2,
        help="Max concurrent school scrapes (default: 2)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would happen — seed nothing, scrape nothing.",
    )
    parser.add_argument(
        "--seed-only",
        action="store_true",
        help="Feed scrape URLs into DB but do not trigger scrape.",
    )
    parser.add_argument(
        "--scrape-only",
        action="store_true",
        help="Skip seeding (URLs already seeded) and just trigger scrape.",
    )
    args = parser.parse_args()

    print("=" * 70)
    print("Failure-batch re-scrape trigger")
    print(f"  JSON      : {args.json}")
    print(f"  tenant_id : {args.tenant_id}")
    print(f"  dry_run   : {args.dry_run}")
    mode = "seed-only" if args.seed_only else "scrape-only" if args.scrape_only else "seed+scrape"
    print(f"  mode      : {mode}")
    print("=" * 70)

    if not args.scrape_only:
        await _seed_scrape_urls(args.json, dry_run=args.dry_run)
        if args.seed_only:
            print("\n--seed-only: URLs fed into DB.")
            print("Re-run without --seed-only to trigger the scrape.")
            return 0

    if not args.seed_only:
        await _trigger_scrape(
            args.json,
            dry_run=args.dry_run,
            tenant_id=args.tenant_id,
            concurrency=args.concurrency,
        )

    print("\nDone.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
