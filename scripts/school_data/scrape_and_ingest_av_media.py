#!/usr/bin/env python3
"""
Scrape + ingest audio/video/YouTube media only, end to end, in one run.

Chains the two existing standalone scripts instead of reimplementing either:

  1. ``run_scrape_districts.run_scrape_districts()`` — crawls confirmed
     ``school_scrape_urls`` and persists new ``ScrapedMedia`` rows. This
     process forces ``settings.SCHOOL_SCRAPER_AV_ONLY_MODE = True`` for its
     own lifetime regardless of the .env value, so documents are never
     touched here — and always runs with ``enqueue=False``, since ingestion
     happens in-process via step 2, not via Celery.
  2. ``ingest_av_media.run()`` — fetches/transcribes/ingests every
     audio/video/YouTube ``ScrapedMedia`` row still ``status="discovered"``
     (whatever step 1 just persisted, plus any left over from earlier
     runs), with per-category (youtube vs audio_video) counts and timing.

Only the ingest step's log lines are written to the log file (matching
ingest_av_media.py); the scrape step's progress prints straight to stdout,
same as running run_scrape_districts.py directly.

Usage:
    # Dry-run the scrape step only (nothing persisted, ingest step skipped)
    python scripts/school_data/scrape_and_ingest_av_media.py --org-codes 07350000 --dry-run

    # Real run: scrape (AV-only, persist) + ingest, for specific districts
    python scripts/school_data/scrape_and_ingest_av_media.py --org-codes 07350000 01700000

    # Real run across every school with active scrape URLs
    python scripts/school_data/scrape_and_ingest_av_media.py --all

    # Cap ingestion for a quick local test
    python scripts/school_data/scrape_and_ingest_av_media.py --org-codes 07350000 --ingest-limit 3
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from app.core.config import settings
from scripts.school_data.ingest_av_media import _print_summary as _print_ingest_summary
from scripts.school_data.ingest_av_media import _setup_logging, logger
from scripts.school_data.ingest_av_media import run as run_ingest_av_media
from scripts.school_data.run_scrape_districts import (
    DEFAULT_JSON_PATH,
    run_scrape_districts,
)


async def scrape_and_ingest(
    *,
    tenant_id: int,
    json_path: Path | None,
    org_codes_arg: list[str] | None,
    crawl_depth: int | None,
    use_playwright: bool | None,
    scrape_concurrency: int,
    dry_run: bool,
    ingest_extensions: list[str],
    ingest_statuses: list[str],
    ingest_concurrency: int,
    ingest_limit: int | None,
) -> dict:
    # Forced for the lifetime of this process — this script's whole purpose
    # is AV-only, so it does not rely on whoever set (or forgot to set) the
    # env var, unlike running run_scrape_districts.py directly.
    settings.SCHOOL_SCRAPER_AV_ONLY_MODE = True

    print("=" * 70)
    print("Step 1/2 — scrape (AV-only, persist only, no Celery enqueue)")
    print("=" * 70)
    scrape_result = await run_scrape_districts(
        tenant_id=tenant_id,
        json_path=json_path,
        org_codes_arg=org_codes_arg,
        crawl_depth=crawl_depth,
        use_playwright=use_playwright,
        concurrency=scrape_concurrency,
        dry_run=dry_run,
        enqueue=False,
    )

    if dry_run:
        logger.info("Dry-run requested — skipping ingest step.")
        return {"scrape": scrape_result}

    print()
    logger.info("=" * 70)
    logger.info("Step 2/2 — ingest (fetch + transcribe + ingest, in-process)")
    logger.info("=" * 70)
    ingest_stats = await run_ingest_av_media(
        extensions=ingest_extensions,
        statuses=ingest_statuses,
        school_id=None,
        limit=ingest_limit,
        concurrency=ingest_concurrency,
        dry_run=False,
    )
    _print_ingest_summary(ingest_stats)

    return {"scrape": scrape_result, "ingest": ingest_stats}


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Scrape + ingest audio/video/YouTube media only, end to end."
    )
    # Scrape-side args mirror run_scrape_districts.py.
    parser.add_argument("--json", type=Path, default=DEFAULT_JSON_PATH)
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--org-codes", nargs="+")
    parser.add_argument("--tenant-id", type=int, default=settings.DEFAULT_TENANT_ID)
    parser.add_argument("--crawl-depth", type=int, default=2)
    parser.add_argument("--use-playwright", action="store_true")
    parser.add_argument("--scrape-concurrency", type=int, default=2)
    parser.add_argument("--dry-run", action="store_true")
    # Ingest-side args mirror ingest_av_media.py.
    parser.add_argument(
        "--ingest-extensions",
        type=str,
        default=None,
        help="Comma list (default: .mp4,.webm,.mp3)",
    )
    parser.add_argument(
        "--ingest-statuses",
        type=str,
        default="discovered",
        help="Comma list of scraped_media.status values to ingest (default: discovered)",
    )
    parser.add_argument("--ingest-concurrency", type=int, default=2)
    parser.add_argument("--ingest-limit", type=int, default=None)
    parser.add_argument("--log-dir", type=str, default="logs")
    args = parser.parse_args()

    json_path = None if args.all else args.json
    use_playwright = True if args.use_playwright else None
    ingest_extensions = (
        [e.strip() for e in args.ingest_extensions.split(",")]
        if args.ingest_extensions
        else [".mp4", ".webm", ".mp3"]
    )
    ingest_statuses = [s.strip() for s in args.ingest_statuses.split(",")]

    log_path = _setup_logging(Path(args.log_dir))
    logger.info("Log file: %s", log_path.resolve())

    try:
        asyncio.run(
            scrape_and_ingest(
                tenant_id=args.tenant_id,
                json_path=json_path,
                org_codes_arg=args.org_codes,
                crawl_depth=args.crawl_depth,
                use_playwright=use_playwright,
                scrape_concurrency=args.scrape_concurrency,
                dry_run=args.dry_run,
                ingest_extensions=ingest_extensions,
                ingest_statuses=ingest_statuses,
                ingest_concurrency=args.ingest_concurrency,
                ingest_limit=args.ingest_limit,
            )
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("Run failed: %s", exc)
        sys.exit(1)


if __name__ == "__main__":
    main()
