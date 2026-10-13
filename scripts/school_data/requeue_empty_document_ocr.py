#!/usr/bin/env python3
"""Re-queue empty document scraped_media for OCR ingest (small batches).

When ENABLE_OCR was off, scanned/image PDFs failed the empty-text guard in
``ingest_scraped_media`` and were stored as ``status='no_transcript'`` with
``error_message='transcript was empty'`` — **no Document row was created**.
``reprocess_failed_documents.py`` cannot see those rows.

This script resets document rows back to ``discovered`` and enqueues
``ingest_scraped_media`` so OCR can recover text.

Modes:
  - one-shot: ``--limit N`` (default) — enqueue one batch and exit
  - overnight: ``--until-empty`` — enqueue N, wait for that batch to leave
    in-flight statuses, repeat until the pool is drained (or every remaining
    candidate has failed once this run)

Prod stores ``file_extension`` with a leading dot (``.pdf``).

By default only filenames matching meeting-doc keywords (meeting / minutes /
agenda / packet) are requeued. Pass ``--no-name-filter`` to include all
empty PDFs, or ``--name-keywords`` to override the default set.

Usage (prod — start once, walk away):

    docker compose -f docker-compose.prod.yml stop celery-beat

    docker exec -d just-edtech-api sh -c 'python \\
      scripts/school_data/requeue_empty_document_ocr.py \\
      --tenant-id 4 --limit 3 --until-empty \\
      > /tmp/ocr_requeue.log 2>&1'

    # Tail progress
    docker exec just-edtech-api tail -f /tmp/ocr_requeue.log

One-shot / dry-run:

    docker exec just-edtech-api python \\
      scripts/school_data/requeue_empty_document_ocr.py --tenant-id 4 --dry-run
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
from datetime import datetime, timezone

from sqlalchemy import func, or_, select

from app.core.config import settings
from app.db.connector import AsyncSessionLocal
from app.models.school import ScrapedMedia

# Rows we can recover with OCR. A/V no_transcript is a different plan.
DOC_MEDIA_TYPES = ("document",)
# Prod stores leading-dot extensions (".pdf"); accept both forms via normalize.
DEFAULT_EXTS = (".pdf",)
# Prefer board meeting docs for the bulk OCR pass (original_name ILIKE).
DEFAULT_NAME_KEYWORDS = ("meeting", "minutes", "agenda", "packet")

# Statuses while ingest_scraped_media / early pipeline is still working the row.
IN_FLIGHT = frozenset({"discovered", "downloading", "ingesting"})


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
        for variant in (
            bare,
            f".{bare}",
            bare.lower(),
            bare.upper(),
            f".{bare.lower()}",
            f".{bare.upper()}",
        ):
            out.add(variant)
    return tuple(sorted(out))


def _normalize_name_keywords(keywords: tuple[str, ...]) -> tuple[str, ...]:
    """Dedupe and strip; preserve order of first occurrence."""
    seen: set[str] = set()
    out: list[str] = []
    for raw in keywords:
        kw = (raw or "").strip().lower()
        if not kw or kw in seen:
            continue
        seen.add(kw)
        out.append(kw)
    return tuple(out)


def _name_keyword_clause(keywords: tuple[str, ...] | None):
    """Case-insensitive substring match on original_name, or None if disabled."""
    if not keywords:
        return None
    return or_(
        *(ScrapedMedia.original_name.ilike(f"%{kw}%") for kw in keywords)
    )


def _ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


async def count_remaining(
    tenant_id: int,
    exts: tuple[str, ...],
    exclude_ids: set[int] | None = None,
    name_keywords: tuple[str, ...] | None = None,
) -> int:
    async with AsyncSessionLocal() as db:
        stmt = (
            select(func.count())
            .select_from(ScrapedMedia)
            .where(
                ScrapedMedia.tenant_id == tenant_id,
                ScrapedMedia.status == "no_transcript",
                ScrapedMedia.media_type.in_(DOC_MEDIA_TYPES),
                ScrapedMedia.file_extension.in_(exts),
            )
        )
        name_clause = _name_keyword_clause(name_keywords)
        if name_clause is not None:
            stmt = stmt.where(name_clause)
        if exclude_ids:
            stmt = stmt.where(ScrapedMedia.id.notin_(exclude_ids))
        return (await db.execute(stmt)).scalar_one()


async def fetch_batch_ids(
    tenant_id: int,
    limit: int,
    exts: tuple[str, ...],
    exclude_ids: set[int],
    name_keywords: tuple[str, ...] | None = None,
) -> list[dict]:
    async with AsyncSessionLocal() as db:
        stmt = (
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
        name_clause = _name_keyword_clause(name_keywords)
        if name_clause is not None:
            stmt = stmt.where(name_clause)
        if exclude_ids:
            stmt = stmt.where(ScrapedMedia.id.notin_(exclude_ids))
        rows = (await db.execute(stmt)).scalars().all()
        return [
            {
                "id": sm.id,
                "school_org_code": sm.school_org_code,
                "file_extension": sm.file_extension,
                "original_name": sm.original_name,
            }
            for sm in rows
        ]


async def statuses_for(ids: list[int]) -> dict[int, str]:
    if not ids:
        return {}
    async with AsyncSessionLocal() as db:
        rows = (
            await db.execute(
                select(ScrapedMedia.id, ScrapedMedia.status).where(
                    ScrapedMedia.id.in_(ids)
                )
            )
        ).all()
        return {int(i): s for i, s in rows}


async def enqueue_batch(batch: list[dict]) -> list[int]:
    from app.crud.schools import update_scraped_media
    from app.tasks.school_scraper_tasks import ingest_scraped_media

    enqueued: list[int] = []
    for item in batch:
        label = (
            f"id={item['id']} school={item['school_org_code']} "
            f"ext={item['file_extension']} "
            f"name={(item['original_name'] or '')[:50]}"
        )
        async with AsyncSessionLocal() as db:
            # Clear error with "" — update_scraped_media skips None.
            await update_scraped_media(
                db,
                item["id"],
                status="discovered",
                error_message="",
            )
        ingest_scraped_media.delay(scraped_media_id=item["id"])
        enqueued.append(item["id"])
        print(f"  [{_ts()}] enqueued {label}", flush=True)
    return enqueued


async def wait_for_batch(
    ids: list[int],
    *,
    poll_seconds: float,
    batch_timeout_seconds: float,
) -> dict[str, list[int]]:
    """Block until no id is still in-flight, or timeout.

    Returns buckets: completed-like, failed_again (back to no_transcript),
    other terminal, still_in_flight (only on timeout).
    """
    deadline = time.monotonic() + batch_timeout_seconds
    while True:
        current = await statuses_for(ids)
        in_flight = [i for i, s in current.items() if s in IN_FLIGHT]
        if not in_flight:
            break
        if time.monotonic() >= deadline:
            print(
                f"  [{_ts()}] WARN batch timeout after {batch_timeout_seconds:.0f}s; "
                f"still in-flight: {in_flight}",
                flush=True,
            )
            break
        print(
            f"  [{_ts()}] waiting on {len(in_flight)} in-flight "
            f"(statuses={dict((i, current[i]) for i in in_flight)}) "
            f"sleep={poll_seconds}s",
            flush=True,
        )
        await asyncio.sleep(poll_seconds)

    current = await statuses_for(ids)
    buckets: dict[str, list[int]] = {
        "ok": [],
        "failed_again": [],
        "other": [],
        "still_in_flight": [],
    }
    for i in ids:
        s = current.get(i, "missing")
        if s in IN_FLIGHT:
            buckets["still_in_flight"].append(i)
        elif s == "no_transcript":
            buckets["failed_again"].append(i)
        elif s in {"completed", "ingested"}:
            buckets["ok"].append(i)
        else:
            buckets["other"].append(i)
            print(f"  [{_ts()}] id={i} finished as status={s}", flush=True)
    return buckets


async def run_until_empty(
    *,
    tenant_id: int,
    limit: int,
    exts: tuple[str, ...],
    name_keywords: tuple[str, ...] | None,
    poll_seconds: float,
    batch_timeout_seconds: float,
    max_batches: int | None,
) -> None:
    skip_ids: set[int] = set()
    batch_num = 0
    totals = {"enqueued": 0, "ok": 0, "failed_again": 0, "other": 0, "timeout": 0}
    kw_label = ", ".join(name_keywords) if name_keywords else "(none — all names)"

    print("=" * 60, flush=True)
    print("Empty-document OCR re-queue — UNTIL EMPTY", flush=True)
    print(f"  tenant_id              : {tenant_id}", flush=True)
    print(f"  limit (batch size)     : {limit}", flush=True)
    print(f"  extensions             : {', '.join(exts)}", flush=True)
    print(f"  name_keywords          : {kw_label}", flush=True)
    print(f"  ENABLE_OCR             : {settings.ENABLE_OCR}", flush=True)
    print(f"  OCR_DPI                : {settings.OCR_DPI}", flush=True)
    print(f"  poll_seconds           : {poll_seconds}", flush=True)
    print(f"  batch_timeout_seconds  : {batch_timeout_seconds}", flush=True)
    print(f"  max_batches            : {max_batches or 'unlimited'}", flush=True)
    print("=" * 60, flush=True)

    if not settings.ENABLE_OCR:
        print("ABORT: ENABLE_OCR is False.", file=sys.stderr, flush=True)
        sys.exit(2)

    while True:
        remaining = await count_remaining(
            tenant_id, exts, exclude_ids=skip_ids, name_keywords=name_keywords
        )
        print(
            f"\n[{_ts()}] remaining (excl. skip)={remaining}  "
            f"skipped_this_run={len(skip_ids)}",
            flush=True,
        )
        if remaining == 0:
            print(f"[{_ts()}] Done — pool drained (or only skips left).", flush=True)
            break

        if max_batches is not None and batch_num >= max_batches:
            print(
                f"[{_ts()}] Stopped at max_batches={max_batches}. "
                f"remaining={remaining}",
                flush=True,
            )
            break

        batch_num += 1
        batch = await fetch_batch_ids(
            tenant_id, limit, exts, skip_ids, name_keywords=name_keywords
        )
        if not batch:
            print(f"[{_ts()}] No rows left to enqueue.", flush=True)
            break

        print(f"[{_ts()}] === batch {batch_num} size={len(batch)} ===", flush=True)
        ids = await enqueue_batch(batch)
        totals["enqueued"] += len(ids)

        buckets = await wait_for_batch(
            ids,
            poll_seconds=poll_seconds,
            batch_timeout_seconds=batch_timeout_seconds,
        )
        totals["ok"] += len(buckets["ok"])
        totals["failed_again"] += len(buckets["failed_again"])
        totals["other"] += len(buckets["other"])
        totals["timeout"] += len(buckets["still_in_flight"])

        # Do not re-pick rows that failed OCR again or timed out this run.
        for i in buckets["failed_again"] + buckets["still_in_flight"]:
            skip_ids.add(i)

        print(
            f"[{_ts()}] batch {batch_num} summary: "
            f"ok={len(buckets['ok'])} failed_again={len(buckets['failed_again'])} "
            f"other={len(buckets['other'])} timeout={len(buckets['still_in_flight'])}",
            flush=True,
        )

    print("\n=== run totals ===", flush=True)
    for k, v in totals.items():
        print(f"  {k:<16}: {v}", flush=True)
    print(f"  skipped_ids     : {len(skip_ids)}", flush=True)
    final = await count_remaining(tenant_id, exts, name_keywords=name_keywords)
    print(f"  remaining_raw   : {final}  (incl. failed_again still no_transcript)", flush=True)


async def requeue_once(
    *,
    tenant_id: int,
    limit: int,
    dry_run: bool,
    exts: tuple[str, ...],
    name_keywords: tuple[str, ...] | None,
) -> dict:
    remaining = await count_remaining(tenant_id, exts, name_keywords=name_keywords)
    kw_label = ", ".join(name_keywords) if name_keywords else "(none — all names)"

    print("=" * 60)
    print("Empty-document OCR re-queue")
    print(f"  tenant_id     : {tenant_id}")
    print(f"  dry_run       : {dry_run}")
    print(f"  limit         : {limit}")
    print(f"  extensions    : {', '.join(exts)}")
    print(f"  name_keywords : {kw_label}")
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

    batch = await fetch_batch_ids(
        tenant_id, limit, exts, exclude_ids=set(), name_keywords=name_keywords
    )
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

    if dry_run:
        for item in batch:
            print(
                f"  [dry]  id={item['id']} school={item['school_org_code']} "
                f"ext={item['file_extension']} "
                f"name={(item['original_name'] or '')[:50]}"
            )
            stats["dry_run"] += 1
        return stats

    ids = await enqueue_batch(batch)
    stats["enqueued"] = len(ids)
    left = await count_remaining(tenant_id, exts, name_keywords=name_keywords)
    print("\nBatch results:")
    for k, v in stats.items():
        print(f"  {k:<18}: {v}")
    print(f"  {'remaining_after':<18}: {left}")
    return stats


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tenant-id", type=int, default=4)
    parser.add_argument(
        "--limit",
        type=int,
        default=3,
        help="Max rows per batch (default: 3). Keep small for OCR/OOM.",
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--until-empty",
        action="store_true",
        help="Loop: enqueue --limit, wait for drain, repeat until pool empty.",
    )
    parser.add_argument(
        "--poll-seconds",
        type=float,
        default=45.0,
        help="Seconds between in-flight status checks (until-empty only).",
    )
    parser.add_argument(
        "--batch-timeout-seconds",
        type=float,
        default=3600.0,
        help="Max wait per batch before skipping stuck ids (until-empty only).",
    )
    parser.add_argument(
        "--max-batches",
        type=int,
        default=None,
        help="Optional cap on batches this run (until-empty only).",
    )
    parser.add_argument(
        "--ext",
        action="append",
        dest="exts",
        default=None,
        help="File extension filter (repeatable). Default: .pdf.",
    )
    parser.add_argument(
        "--name-keywords",
        action="append",
        dest="name_keywords",
        default=None,
        help=(
            "Substring filter on original_name (repeatable, case-insensitive). "
            f"Default: {', '.join(DEFAULT_NAME_KEYWORDS)}."
        ),
    )
    parser.add_argument(
        "--no-name-filter",
        action="store_true",
        help="Disable filename keyword filter (requeue all empty PDFs).",
    )
    args = parser.parse_args()
    exts = _normalize_exts(tuple(args.exts) if args.exts else DEFAULT_EXTS)

    if args.no_name_filter and args.name_keywords:
        print(
            "Cannot combine --no-name-filter with --name-keywords",
            file=sys.stderr,
        )
        sys.exit(2)

    if args.no_name_filter:
        name_keywords: tuple[str, ...] | None = None
    elif args.name_keywords:
        name_keywords = _normalize_name_keywords(tuple(args.name_keywords))
        if not name_keywords:
            print("Empty --name-keywords after normalize.", file=sys.stderr)
            sys.exit(2)
    else:
        name_keywords = DEFAULT_NAME_KEYWORDS

    if args.until_empty and args.dry_run:
        print("Cannot combine --until-empty with --dry-run", file=sys.stderr)
        sys.exit(2)

    try:
        if args.until_empty:
            asyncio.run(
                run_until_empty(
                    tenant_id=args.tenant_id,
                    limit=args.limit,
                    exts=exts,
                    name_keywords=name_keywords,
                    poll_seconds=args.poll_seconds,
                    batch_timeout_seconds=args.batch_timeout_seconds,
                    max_batches=args.max_batches,
                )
            )
        else:
            asyncio.run(
                requeue_once(
                    tenant_id=args.tenant_id,
                    limit=args.limit,
                    dry_run=args.dry_run,
                    exts=exts,
                    name_keywords=name_keywords,
                )
            )
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        sys.exit(130)
    except Exception as exc:
        print(f"\nRe-queue failed: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
