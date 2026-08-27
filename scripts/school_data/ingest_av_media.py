#!/usr/bin/env python3
"""
Fetch + ingest YouTube and audio/video ScrapedMedia rows, with per-category
counts and timing.

Reuses the real pipeline (`_ingest_scraped_media_async` in
app/tasks/school_scraper_tasks.py) directly in-process instead of going
through Celery/Redis, so it can run standalone against a DB (local or
production) and still get exact per-item timing. All cost/behavior knobs
(ASSEMBLYAI_API_KEY, SCHOOL_SCRAPER_YOUTUBE_TRANSCRIPT_ENABLED,
TRANSCRIPTION_AUDIO_MODE, SCHOOL_SCRAPER_MEDIA_MAX_DURATION_MINUTES, etc.)
are the same app.core.config.settings the API/Celery workers use, so the
script behaves the same in local and production as long as the environment
is configured the same way.

Env vars (all optional, all have safe local-friendly defaults):
    AV_INGEST_EXTENSIONS   Comma list of file extensions treated as
                            "audio/video" (default: .mp4,.webm,.mp3)
    AV_INGEST_STATUSES     Comma list of scraped_media.status values to pick
                            up (default: discovered). Include "failed" to
                            retry previous errors, e.g. "discovered,failed".
    AV_INGEST_SCHOOL_ID    Optional single school_id filter.
    AV_INGEST_LIMIT        Optional cap on number of rows processed (testing).
    AV_INGEST_CONCURRENCY  Max concurrent ingest calls (default: 2 — matches
                            the celery-scraper concurrency=1-per-replica
                            guidance since transcription is memory-heavy).
    AV_INGEST_DRY_RUN      "true"/"1" to only report what would run.
    AV_INGEST_LOG_DIR      Directory for the run's log file (default: logs).

Usage:
    # Local dry-run, see what would be picked up
    AV_INGEST_DRY_RUN=true python scripts/school_data/ingest_av_media.py

    # Local real run, small batch
    AV_INGEST_LIMIT=5 python scripts/school_data/ingest_av_media.py

    # Production run (same script, env vars set in the prod environment)
    python scripts/school_data/ingest_av_media.py

    # CLI flags override the env vars above if both are given
    python scripts/school_data/ingest_av_media.py --dry-run --limit 5 --concurrency 3
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import or_, select

from app.db.connector import AsyncSessionLocal
from app.models.school import ScrapedMedia

YOUTUBE = "youtube"
AUDIO_VIDEO = "audio_video"

logger = logging.getLogger("ingest_av_media")


def _env_bool(name: str, default: bool) -> bool:
    val = os.getenv(name)
    if val is None:
        return default
    return val.strip().lower() in ("1", "true", "yes", "on")


def _env_list(name: str, default: list[str]) -> list[str]:
    val = os.getenv(name)
    if not val:
        return default
    return [item.strip() for item in val.split(",") if item.strip()]


def _env_int(name: str, default: int | None) -> int | None:
    val = os.getenv(name)
    if not val:
        return default
    return int(val)


def _setup_logging(log_dir: Path) -> Path:
    log_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    log_path = log_dir / f"ingest_av_media_{stamp}.log"

    formatter = logging.Formatter(
        fmt="%(asctime)s | %(levelname)-7s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setFormatter(formatter)

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)

    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(file_handler)
    root.addHandler(console_handler)

    return log_path


def _new_bucket() -> dict:
    return {
        "discovered": 0,
        "fetched": 0,
        "ingested": 0,
        "skipped": 0,
        "error": 0,
        "seconds": 0.0,
    }


def _classify(status: str, error_message: str | None) -> str:
    """Map a final ScrapedMedia.status onto one of our reporting buckets.

    Returns one of: "ingested", "fetched_only", "skipped", "fetched_error",
    "error". "fetched" (for counting purposes) is true for the first three.
    """
    if status == "completed":
        return "ingested"
    if status in ("no_transcript", "skipped_duplicate"):
        # Materialize succeeded (transcript fetched / duplicate detected via
        # content hash computed post-fetch) but nothing new was ingested.
        return "fetched_only"
    if status == "skipped_year":
        # Decided before any fetch attempt.
        return "skipped"
    if status == "failed" and error_message and error_message.startswith(
        "post-transcription persist failed"
    ):
        # Fetch succeeded; only the post-fetch persist step failed.
        return "fetched_error"
    # "failed" (generic) or any other terminal transcription status
    # (e.g. too_long, download_failed) — fetch itself did not succeed.
    return "error"


def _apply_classification(bucket: dict, classification: str) -> None:
    if classification == "ingested":
        bucket["fetched"] += 1
        bucket["ingested"] += 1
    elif classification == "fetched_only":
        bucket["fetched"] += 1
        bucket["skipped"] += 1
    elif classification == "skipped":
        bucket["skipped"] += 1
    elif classification == "fetched_error":
        bucket["fetched"] += 1
        bucket["error"] += 1
    else:  # "error"
        bucket["error"] += 1


async def _fetch_candidate_rows(
    *,
    extensions: list[str],
    statuses: list[str],
    school_id: int | None,
    limit: int | None,
) -> list[ScrapedMedia]:
    async with AsyncSessionLocal() as db:
        conditions = [
            ScrapedMedia.status.in_(statuses),
            or_(
                ScrapedMedia.media_type == YOUTUBE,
                ScrapedMedia.file_extension.in_(extensions),
            ),
        ]
        if school_id is not None:
            conditions.append(ScrapedMedia.school_id == school_id)

        query = select(ScrapedMedia).where(*conditions).order_by(ScrapedMedia.id.asc())
        if limit:
            query = query.limit(limit)

        result = await db.execute(query)
        return list(result.scalars().all())


async def _ingest_one(sm_id: int, semaphore: asyncio.Semaphore) -> tuple[float, str, str | None]:
    """Run the real pipeline for one row; return (seconds, status, error_message)."""
    from app.tasks.school_scraper_tasks import _ingest_scraped_media_async

    start = time.perf_counter()
    status = "failed"
    error_message = None
    async with semaphore:
        try:
            result = await _ingest_scraped_media_async(sm_id)
            status = result.get("status", "failed")
        except Exception as exc:  # noqa: BLE001
            # _ingest_scraped_media_async already persisted status="failed"
            # + error_message for genuine exceptions before re-raising; we
            # just need to not let one bad row kill the whole batch.
            logger.exception("scraped_media %s raised during ingest", sm_id)
            status = "failed"
            error_message = str(exc)
    elapsed = time.perf_counter() - start

    if error_message is None and status == "failed":
        # Pull the persisted error_message so _classify can tell a
        # post-persist failure (fetch succeeded) from a fetch failure.
        async with AsyncSessionLocal() as db:
            row = await db.get(ScrapedMedia, sm_id)
            error_message = row.error_message if row else None

    return elapsed, status, error_message


async def run(
    *,
    extensions: list[str],
    statuses: list[str],
    school_id: int | None,
    limit: int | None,
    concurrency: int,
    dry_run: bool,
) -> dict:
    logger.info("=" * 70)
    logger.info("YouTube + Audio/Video ingest")
    logger.info("  extensions   : %s", extensions)
    logger.info("  statuses     : %s", statuses)
    logger.info("  school_id    : %s", school_id or "(all)")
    logger.info("  limit        : %s", limit or "(none)")
    logger.info("  concurrency  : %s", concurrency)
    logger.info("  dry_run      : %s", dry_run)
    logger.info("=" * 70)

    rows = await _fetch_candidate_rows(
        extensions=extensions, statuses=statuses, school_id=school_id, limit=limit
    )

    youtube_rows = [r for r in rows if r.media_type == YOUTUBE]
    av_rows = [r for r in rows if r.media_type != YOUTUBE]

    stats = {YOUTUBE: _new_bucket(), AUDIO_VIDEO: _new_bucket()}
    stats[YOUTUBE]["discovered"] = len(youtube_rows)
    stats[AUDIO_VIDEO]["discovered"] = len(av_rows)

    logger.info(
        "Discovered %d candidate rows (%d youtube, %d audio/video)",
        len(rows),
        len(youtube_rows),
        len(av_rows),
    )

    if dry_run:
        for row in rows:
            logger.info(
                "  [dry] id=%s media_type=%s ext=%s status=%s url=%s",
                row.id,
                row.media_type,
                row.file_extension,
                row.status,
                (row.source_media_url or "")[:80],
            )
        return stats

    if not rows:
        logger.info("Nothing to ingest.")
        return stats

    semaphore = asyncio.Semaphore(concurrency)
    overall_start = time.perf_counter()

    async def _process(row: ScrapedMedia) -> None:
        category = YOUTUBE if row.media_type == YOUTUBE else AUDIO_VIDEO
        elapsed, status, error_message = await _ingest_one(row.id, semaphore)
        classification = _classify(status, error_message)
        _apply_classification(stats[category], classification)
        stats[category]["seconds"] += elapsed
        logger.info(
            "  [%s] id=%s category=%s status=%s (%.1fs)%s",
            classification,
            row.id,
            category,
            status,
            elapsed,
            f" — {error_message}" if classification == "error" and error_message else "",
        )

    await asyncio.gather(*(_process(row) for row in rows))

    total_elapsed = time.perf_counter() - overall_start
    stats["_total_wall_seconds"] = total_elapsed
    return stats


def _print_summary(stats: dict) -> None:
    def fmt_seconds(s: float) -> str:
        minutes, seconds = divmod(s, 60)
        return f"{int(minutes)}m {seconds:.1f}s" if minutes else f"{seconds:.1f}s"

    logger.info("")
    logger.info("=" * 70)
    logger.info("RESULTS")
    logger.info("=" * 70)
    header = f"{'category':<14}{'discovered':>11}{'fetched':>9}{'ingested':>10}{'skipped':>9}{'error':>7}{'time':>12}"
    logger.info(header)
    logger.info("-" * len(header))
    for category, label in ((YOUTUBE, "youtube"), (AUDIO_VIDEO, "audio_video")):
        b = stats[category]
        logger.info(
            f"{label:<14}{b['discovered']:>11}{b['fetched']:>9}{b['ingested']:>10}"
            f"{b['skipped']:>9}{b['error']:>7}{fmt_seconds(b['seconds']):>12}"
        )
    logger.info("-" * len(header))
    if "_total_wall_seconds" in stats:
        logger.info(
            "Total wall-clock time (fetch + ingest, concurrent): %s",
            fmt_seconds(stats["_total_wall_seconds"]),
        )
        logger.info(
            "Sum of per-item time — youtube: %s, audio_video: %s",
            fmt_seconds(stats[YOUTUBE]["seconds"]),
            fmt_seconds(stats[AUDIO_VIDEO]["seconds"]),
        )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Fetch + ingest youtube and audio/video ScrapedMedia rows."
    )
    parser.add_argument("--extensions", type=str, default=None, help="Comma list, overrides AV_INGEST_EXTENSIONS")
    parser.add_argument("--statuses", type=str, default=None, help="Comma list, overrides AV_INGEST_STATUSES")
    parser.add_argument("--school-id", type=int, default=None)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--concurrency", type=int, default=None)
    parser.add_argument("--dry-run", action="store_true", default=None)
    parser.add_argument("--log-dir", type=str, default=None)
    args = parser.parse_args()

    extensions = (
        [e.strip() for e in args.extensions.split(",")]
        if args.extensions
        else _env_list("AV_INGEST_EXTENSIONS", [".mp4", ".webm", ".mp3"])
    )
    statuses = (
        [s.strip() for s in args.statuses.split(",")]
        if args.statuses
        else _env_list("AV_INGEST_STATUSES", ["discovered"])
    )
    school_id = args.school_id if args.school_id is not None else _env_int("AV_INGEST_SCHOOL_ID", None)
    limit = args.limit if args.limit is not None else _env_int("AV_INGEST_LIMIT", None)
    concurrency = args.concurrency if args.concurrency is not None else (_env_int("AV_INGEST_CONCURRENCY", 2) or 2)
    dry_run = args.dry_run if args.dry_run is not None else _env_bool("AV_INGEST_DRY_RUN", False)
    log_dir = Path(args.log_dir or os.getenv("AV_INGEST_LOG_DIR", "logs"))

    log_path = _setup_logging(log_dir)
    logger.info("Log file: %s", log_path.resolve())

    try:
        stats = asyncio.run(
            run(
                extensions=extensions,
                statuses=statuses,
                school_id=school_id,
                limit=limit,
                concurrency=concurrency,
                dry_run=dry_run,
            )
        )
        _print_summary(stats)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Run failed: %s", exc)
        sys.exit(1)


if __name__ == "__main__":
    main()
