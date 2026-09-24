#!/usr/bin/env python3
"""Re-queue empty document scraped_media for OCR ingest (small batches).

When ENABLE_OCR was off, scanned/image PDFs failed the empty-text guard in
``ingest_scraped_media`` and were stored as ``status='no_transcript'`` with
``error_message='transcript was empty'`` — **no Document row was created**.
``reprocess_failed_documents.py`` cannot see those rows.

This script resets a **limited** batch of document rows back to
``discovered`` and enqueues ``ingest_scraped_media`` so OCR can recover
text. Designed for overnight runs: enqueue only a few at a time so an OOM
on one batch does not strand a huge broker backlog.

Usage (prod):
    # Count candidates
    docker exec just-edtech-api python \\
      scripts/school_data/requeue_empty_document_ocr.py --tenant-id 4 --dry-run

    # First batch of 5
    docker exec just-edtech-api python \\
      scripts/school_data/requeue_empty_document_ocr.py --tenant-id 4 --limit 5

    # Next batch after the queue drains (repeat overnight)
    docker exec just-edtech-api python \\
      scripts/school_data/requeue_empty_document_ocr.py --tenant-id 4 --limit 5
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from sqlalchemy import func, select

from app.core.config import settings
from app.db.connector import AsyncSessionLocal
from app.models.school import ScrapedMedia

# Rows we can recover with OCR. A/V no_transcript is a different plan.
DOC_MEDIA_TYPES = ("document",)
# Prod stores leading-dot extensions (".pdf"); accept both forms.
DEFAULT_EXTS = (".pdf",)


def _normalize_exts(exts: tuple[str, ...]) -> tuple[str, ...]:
    """Expand user/default exts to with-dot, without-dot, and case variants."""
    out: set[str] = set()
    for raw in exts:
        e = (raw or "").strip()
        if not e:
            continue
        bare = e.lstrip(".")
        if not bare:
            continue
        for variant in (bare, f".{bare}", bare.lower(), bare.upper(), f".{bare.lower()}", f".{bare.upper()}"):
            out.add(variant)
    return tuple(sorted(out))


async def count_remaining(tenant_id: int, exts: tuple[str, ...]) -> int:
    async with AsyncSessionLocal() as db:
        return (
            await db.execute(
                select(func.count())
                .select_from(ScrapedMedia)
                .where(
                    ScrapedMedia.tenant_id == tenant_id,
                    ScrapedMedia.status == "no_transcript",
                    ScrapedMedia.media_type.in_(DOC_MEDIA_TYPES),
                    ScrapedMedia.file_extension.in_(exts),
                )
            )
        ).scalar_one()


async def requeue_batch(
    *,
    tenant_id: int,
    limit: int,
    dry_run: bool,
    exts: tuple[str, ...],
) -> dict:
    from app.crud.schools import update_scraped_media
    from app.tasks.school_scraper_tasks import ingest_scraped_media

    remaining = await count_remaining(tenant_id, exts)

    print("=" * 60)
    print("Empty-document OCR re-queue")
    print(f"  tenant_id     : {tenant_id}")
    print(f"  dry_run       : {dry_run}")
    print(f"  limit         : {limit}")
    print(f"  extensions    : {', '.join(exts)}")
    print(f"  ENABLE_OCR    : {settings.ENABLE_OCR}")
    print(f"  OCR_DPI       : {settings.OCR_DPI}")
    print(f"  remaining     : {remaining}")
    print("=" * 60)

    if not settings.ENABLE_OCR and not dry_run:
        print(
            "ABORT: ENABLE_OCR is False. Recreate workers with OCR on first.",
            file=sys.stderr,
        )
        sys.exit(2)

    async with AsyncSessionLocal() as db:
        rows = (
            (
                await db.execute(
                    select(ScrapedMedia)
                    .where(
                        ScrapedMedia.tenant_id == tenant_id,
                        ScrapedMedia.status == "no_transcript",
                        ScrapedMedia.media_type.in_(DOC_MEDIA_TYPES),
                        ScrapedMedia.file_extension.in_(exts),
                    )
                    .order_by(ScrapedMedia.id.asc())
                    .limit(limit)
                )
            )
            .scalars()
            .all()
        )
        batch = [
            {
                "id": sm.id,
                "school_org_code": sm.school_org_code,
                "file_extension": sm.file_extension,
                "original_name": sm.original_name,
            }
            for sm in rows
        ]

    print(f"  this batch    : {len(batch)}")
    stats = {
        "remaining_before": remaining,
        "batch": len(batch),
        "enqueued": 0,
        "dry_run": 0,
    }

    if not batch:
        print("No matching rows. Nothing to do.")
        return stats

    for item in batch:
        label = (
            f"id={item['id']} school={item['school_org_code']} "
            f"ext={item['file_extension']} "
            f"name={(item['original_name'] or '')[:50]}"
        )
        if dry_run:
            stats["dry_run"] += 1
            print(f"  [dry]  {label}")
            continue

        async with AsyncSessionLocal() as db:
            # Clear error with "" — update_scraped_media skips None.
            await update_scraped_media(
                db,
                item["id"],
                status="discovered",
                error_message="",
            )
        # Enqueue only after commit (update_scraped_media commits).
        ingest_scraped_media.delay(scraped_media_id=item["id"])
        stats["enqueued"] += 1
        print(f"  [ok]   {label}")

    left = await count_remaining(tenant_id, exts)
    print("\nBatch results:")
    for k, v in stats.items():
        print(f"  {k:<18}: {v}")
    print(f"  {'remaining_after':<18}: {left}")
    print(
        "\nNext: wait until scraping+documents queues are idle, "
        "then re-run with the same --limit."
    )
    return stats


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tenant-id", type=int, default=4)
    parser.add_argument(
        "--limit",
        type=int,
        default=5,
        help="Max rows to reset+enqueue this run (default: 5). Keep small for OCR/OOM.",
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--ext",
        action="append",
        dest="exts",
        default=None,
        help="File extension filter (repeatable). Default: pdf only.",
    )
    args = parser.parse_args()
    exts = _normalize_exts(tuple(args.exts) if args.exts else DEFAULT_EXTS)

    try:
        asyncio.run(
            requeue_batch(
                tenant_id=args.tenant_id,
                limit=args.limit,
                dry_run=args.dry_run,
                exts=exts,
            )
        )
    except Exception as exc:
        print(f"\nRe-queue failed: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
