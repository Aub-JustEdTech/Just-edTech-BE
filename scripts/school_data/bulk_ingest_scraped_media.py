#!/usr/bin/env python3
"""
Bulk-dispatch scraped_media rows into the ingestion pipeline.

Run once the upstream scraper has confirmed a district (or the full 400-district
sweep) is discovered — this reads scraped_media rows still in `status`
(default: 'discovered') and enqueues the existing per-item Celery task
(`ingest_scraped_media`) for each one, which downloads/dedupes the file,
creates a Document, and hands it to `process_document_pipeline` (chunk,
embed, store). This script never talks to OpenAI/S3 directly — it only
enqueues Celery tasks that do.

Usage:
    python scripts/school_data/bulk_ingest_scraped_media.py --dry-run
    python scripts/school_data/bulk_ingest_scraped_media.py --tenant-id 2
    python scripts/school_data/bulk_ingest_scraped_media.py --tenant-id 2 --school-id 15
    python scripts/school_data/bulk_ingest_scraped_media.py --tenant-id 2 --limit 50
    python scripts/school_data/bulk_ingest_scraped_media.py --tenant-id 2 --reset-stale-minutes 60
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time

from app.core.config import settings
from app.crud.schools import (
    count_scraped_media_by_status,
    list_scraped_media,
    list_stale_in_progress_media,
    update_scraped_media,
)
from app.db.connector import AsyncSessionLocal


async def _reset_stale(
    *, tenant_id: int, older_than_minutes: int, dry_run: bool
) -> int:
    async with AsyncSessionLocal() as db:
        stale = await list_stale_in_progress_media(db, tenant_id, older_than_minutes)
        if not stale:
            return 0
        print(f"Found {len(stale)} stale in-progress row(s) (> {older_than_minutes}m):")
        for sm in stale:
            print(
                f"  [stale] scraped_media={sm.id} status={sm.status} school_id={sm.school_id}"
            )
            if not dry_run:
                await update_scraped_media(db, sm.id, status="discovered")
        return len(stale)


async def bulk_ingest(
    *,
    tenant_id: int,
    status: str,
    school_id: int | None,
    limit: int | None,
    batch_size: int,
    pause_seconds: float,
    reset_stale_minutes: int,
    dry_run: bool,
) -> dict:
    print("=" * 60)
    print("Bulk Scraped-Media Ingestion")
    print(f"  tenant_id           : {tenant_id}")
    print(f"  status filter       : {status}")
    print(f"  school_id           : {school_id}")
    print(f"  limit               : {limit}")
    print(f"  batch_size          : {batch_size}")
    print(f"  reset_stale_minutes : {reset_stale_minutes}")
    print(f"  dry_run             : {dry_run}")
    print("=" * 60)

    if reset_stale_minutes > 0:
        reset_count = await _reset_stale(
            tenant_id=tenant_id,
            older_than_minutes=reset_stale_minutes,
            dry_run=dry_run,
        )
        print(f"Reset {reset_count} stale row(s) back to 'discovered'.\n")

    async with AsyncSessionLocal() as db:
        before_counts = await count_scraped_media_by_status(db, tenant_id)
    print(f"Status counts before run: {before_counts}\n")

    ingest_task = None
    if not dry_run:
        from app.tasks.school_scraper_tasks import ingest_scraped_media

        ingest_task = ingest_scraped_media

    stats = {"total": 0, "enqueued": 0, "dry_run": 0}
    skip = 0

    while limit is None or stats["total"] < limit:
        page_limit = batch_size
        if limit is not None:
            page_limit = min(batch_size, limit - stats["total"])

        async with AsyncSessionLocal() as db:
            items, total = await list_scraped_media(
                db,
                tenant_id,
                school_id=school_id,
                status=status,
                skip=skip,
                limit=page_limit,
            )

        if not items:
            break

        for sm in items:
            stats["total"] += 1
            if dry_run:
                stats["dry_run"] += 1
                print(
                    f"  [dry]  scraped_media={sm.id} school_id={sm.school_id} "
                    f"media_type={sm.media_type} url={sm.source_media_url[:80]}"
                )
                continue

            ingest_task.delay(sm.id)
            stats["enqueued"] += 1
            print(f"  [ok]   scraped_media={sm.id} school_id={sm.school_id} enqueued")

        skip += len(items)
        if skip >= total:
            break
        if pause_seconds > 0:
            time.sleep(pause_seconds)

    print("\nBulk ingest results:")
    for k, v in stats.items():
        print(f"  {k:<12}: {v}")
    return stats


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Bulk-dispatch scraped_media rows into the ingestion pipeline."
    )
    parser.add_argument("--tenant-id", type=int, default=settings.DEFAULT_TENANT_ID)
    parser.add_argument("--status", default="discovered")
    parser.add_argument("--school-id", type=int, default=None)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--batch-size", type=int, default=200)
    parser.add_argument(
        "--pause-seconds",
        type=float,
        default=0.0,
        help="Pause between dispatch pages, to throttle downstream load.",
    )
    parser.add_argument(
        "--reset-stale-minutes",
        type=int,
        default=0,
        help=(
            "Reset rows stuck in 'downloading'/'ingesting' longer than this "
            "many minutes back to 'discovered' before dispatching. 0 disables."
        ),
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    try:
        asyncio.run(
            bulk_ingest(
                tenant_id=args.tenant_id,
                status=args.status,
                school_id=args.school_id,
                limit=args.limit,
                batch_size=args.batch_size,
                pause_seconds=args.pause_seconds,
                reset_stale_minutes=args.reset_stale_minutes,
                dry_run=args.dry_run,
            )
        )
    except Exception as exc:
        print(f"\nBulk ingest failed: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
