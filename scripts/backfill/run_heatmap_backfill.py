"""
Heatmap backfill orchestrator.

Run a one-off backfill of the heatmap ingestion pipeline:

  1. Verify charter_district_mapping is loaded (row count == expected).
  2. Trigger a scrape cycle for the given tenant (if not already run).
  3. Poll ingest progress via ScrapedMedia.status + Document.processing_status.
  4. Once ~95% of docs are completed and pending_classifications is
     populated, manually kick off submit_pending_batch_classification.
  5. Wait for the batch job to hit 'applied', then run reconciliation.
  6. Print a summary report: docs by school, chunks classified, per-topic
     counts, heatmap_aggregate row count.

Usage:
  poetry run python -m scripts.backfill.run_heatmap_backfill --tenant-id 1
  poetry run python -m scripts.backfill.run_heatmap_backfill --tenant-id 1 --skip-scrape
  poetry run python -m scripts.backfill.run_heatmap_backfill --tenant-id 1 --submit-batch
  poetry run python -m scripts.backfill.run_heatmap_backfill --tenant-id 1 --reconcile-only

Environment: requires OPENAI_API_KEY, S3 creds, and a running Qdrant.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import time
from collections import Counter

from sqlalchemy import func, select

from app.db.connector import AsyncSessionLocal
from app.models.batch_classification_job import BatchClassificationJob
from app.models.charter_district_mapping import CharterDistrictMapping
from app.models.documents import Document, ProcessingStatus
from app.models.heatmap_aggregate import HeatmapAggregate
from app.models.pending_classification import PendingClassification
from app.models.school import School, ScrapedMedia
from app.services.heatmap_ingest.taxonomy import TOPICS

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("heatmap_backfill")

# 72 charter districts expected for MA.
EXPECTED_CHARTER_MAPPINGS = 72


# ---------------------------------------------------------------------------
# Step 1: Verify charter mapping
# ---------------------------------------------------------------------------


async def verify_charter_mapping(tenant_id: int) -> dict:
    """Confirm charter_district_mapping is populated."""
    async with AsyncSessionLocal() as db:
        mapping_count = (
            await db.execute(select(func.count()).select_from(CharterDistrictMapping))
        ).scalar_one()

        charter_schools_count = (
            await db.execute(
                select(func.count())
                .select_from(School)
                .where(
                    School.tenant_id == tenant_id,
                    func.lower(School.district_type).contains("charter"),
                )
            )
        ).scalar_one()

    ok = mapping_count >= EXPECTED_CHARTER_MAPPINGS
    logger.info(
        f"[1/6] Charter mapping: {mapping_count} rows (expected ~{EXPECTED_CHARTER_MAPPINGS}), "
        f"{charter_schools_count} charter schools in tenant {tenant_id}. "
        f"{'OK' if ok else 'WARNING: mapping may be incomplete'}"
    )
    return {
        "mapping_count": mapping_count,
        "charter_schools_count": charter_schools_count,
        "ok": ok,
    }


# ---------------------------------------------------------------------------
# Step 2: Scrape cycle (removed — use offline scripts + --skip-scrape)
# ---------------------------------------------------------------------------


def trigger_scrape_cycle(tenant_id: int) -> dict:
    """Scrape cycles are no longer dispatched via Celery.

    Run discovery/scrape offline (scripts/school_data/) then pass
    --skip-scrape to this backfill orchestrator.
    """
    raise RuntimeError(
        "Automated scrape cycles were removed. Run scraping via "
        "scripts/school_data/ and re-run with --skip-scrape."
    )


# ---------------------------------------------------------------------------
# Step 3: Poll ingest progress
# ---------------------------------------------------------------------------


async def poll_ingest_progress(
    tenant_id: int, *, target_pct: float = 0.95, max_wait_s: int = 6 * 3600
) -> dict:
    """
    Poll ScrapedMedia + Document status until target_pct are completed.

    Returns final stats dict. Times out after max_wait_s.
    """
    start = time.time()
    last_log = 0.0
    while True:
        async with AsyncSessionLocal() as db:
            # Total scraped media for this tenant.
            total = (
                await db.execute(
                    select(func.count())
                    .select_from(ScrapedMedia)
                    .where(ScrapedMedia.tenant_id == tenant_id)
                )
            ).scalar_one()

            ingested = (
                await db.execute(
                    select(func.count())
                    .select_from(ScrapedMedia)
                    .where(
                        ScrapedMedia.tenant_id == tenant_id,
                        ScrapedMedia.status == "ingested",
                    )
                )
            ).scalar_one()

            # Document processing status.
            doc_total = (
                await db.execute(
                    select(func.count())
                    .select_from(Document)
                    .where(
                        Document.tenant_id == tenant_id,
                        Document.source_type == "school_scraper",
                    )
                )
            ).scalar_one()

            doc_completed = (
                await db.execute(
                    select(func.count())
                    .select_from(Document)
                    .where(
                        Document.tenant_id == tenant_id,
                        Document.source_type == "school_scraper",
                        Document.processing_status == ProcessingStatus.COMPLETED,
                    )
                )
            ).scalar_one()

            doc_failed = (
                await db.execute(
                    select(func.count())
                    .select_from(Document)
                    .where(
                        Document.tenant_id == tenant_id,
                        Document.source_type == "school_scraper",
                        Document.processing_status == ProcessingStatus.FAILED,
                    )
                )
            ).scalar_one()

            pending_chunks = (
                await db.execute(
                    select(func.count())
                    .select_from(PendingClassification)
                    .where(PendingClassification.status == "pending")
                )
            ).scalar_one()

        elapsed = time.time() - start
        pct = (doc_completed / doc_total) if doc_total else 0.0
        if elapsed - last_log >= 30:
            logger.info(
                f"[3/6] Ingest progress: {doc_completed}/{doc_total} docs "
                f"completed ({pct:.1%}), {doc_failed} failed, "
                f"{ingested}/{total} media ingested, "
                f"{pending_chunks} chunks pending classification "
                f"(elapsed {int(elapsed)}s)"
            )
            last_log = elapsed

        if pct >= target_pct or (doc_total > 0 and doc_completed + doc_failed >= doc_total):
            logger.info(
                f"[3/6] Ingest reached target: {pct:.1%} completed "
                f"({pending_chunks} chunks pending classification)"
            )
            return {
                "doc_total": doc_total,
                "doc_completed": doc_completed,
                "doc_failed": doc_failed,
                "pending_chunks": pending_chunks,
                "elapsed_s": int(elapsed),
            }

        if elapsed > max_wait_s:
            logger.warning(
                f"[3/6] Ingest poll timed out after {max_wait_s}s "
                f"(pct={pct:.1%}); proceeding anyway"
            )
            return {
                "doc_total": doc_total,
                "doc_completed": doc_completed,
                "doc_failed": doc_failed,
                "pending_chunks": pending_chunks,
                "elapsed_s": int(elapsed),
                "timeout": True,
            }

        await asyncio.sleep(30)


# ---------------------------------------------------------------------------
# Step 4: Submit batch classification
# ---------------------------------------------------------------------------


def submit_batch() -> dict:
    """Trigger submit_pending_batch_classification synchronously."""
    from app.tasks.batch_classification_tasks import (
        submit_pending_batch_classification_task,
    )

    result = submit_pending_batch_classification_task.apply().get()
    logger.info(f"[4/6] Batch submit result: {result}")
    return result


# ---------------------------------------------------------------------------
# Step 5: Wait for batch to be applied
# ---------------------------------------------------------------------------


async def wait_for_batch_applied(
    batch_id: str | None = None, *, max_wait_s: int = 30 * 3600
) -> dict:
    """Poll batch jobs until they hit 'applied' (or fail)."""
    start = time.time()
    last_log = 0.0
    while True:
        async with AsyncSessionLocal() as db:
            if batch_id:
                jobs = [
                    (
                        await db.execute(
                            select(BatchClassificationJob).where(
                                BatchClassificationJob.batch_id == batch_id
                            )
                        )
                    ).scalar_one()
                ]
            else:
                jobs = list(
                    (
                        await db.execute(select(BatchClassificationJob))
                    ).scalars().all()
                )

        if not jobs:
            logger.info("[5/6] No batch jobs found; nothing to wait for")
            return {"batches": 0}

        statuses = Counter(j.status for j in jobs)
        elapsed = time.time() - start
        if elapsed - last_log >= 60:
            logger.info(f"[5/6] Batch statuses: {dict(statuses)} (elapsed {int(elapsed)}s)")
            last_log = elapsed

        if all(s in ("applied", "failed", "expired", "cancelled") for s in statuses):
            logger.info(f"[5/6] All batches done: {dict(statuses)}")
            return {
                "batches": len(jobs),
                "statuses": dict(statuses),
                "applied": statuses.get("applied", 0),
                "failed": statuses.get("failed", 0),
            }

        if elapsed > max_wait_s:
            logger.warning(f"[5/6] Batch wait timed out after {max_wait_s}s")
            return {"batches": len(jobs), "statuses": dict(statuses), "timeout": True}

        await asyncio.sleep(60)


# ---------------------------------------------------------------------------
# Step 6: Reconcile + summary
# ---------------------------------------------------------------------------


def run_reconciliation(tenant_id: int) -> dict:
    """Trigger reconcile_heatmap_aggregate synchronously."""
    from app.tasks.heatmap_reconciliation_tasks import (
        reconcile_heatmap_aggregate_task,
    )

    result = reconcile_heatmap_aggregate_task.apply(args=[tenant_id]).get()
    logger.info(f"[6/6] Reconciliation result: {result}")
    return result


async def print_summary(tenant_id: int) -> dict:
    """Print the final summary report."""
    async with AsyncSessionLocal() as db:
        # Docs by school.
        school_doc_counts = (
            await db.execute(
                select(
                    School.name,
                    func.count(Document.id),
                )
                .join(ScrapedMedia, ScrapedMedia.school_id == School.id)
                .join(Document, Document.id == ScrapedMedia.document_id)
                .where(
                    School.tenant_id == tenant_id,
                    Document.source_type == "school_scraper",
                )
                .group_by(School.name)
                .order_by(func.count(Document.id).desc())
                .limit(10)
            )
        ).all()

        # Chunks classified.
        pending_total = (
            await db.execute(select(func.count()).select_from(PendingClassification))
        ).scalar_one()
        pending_by_status = dict(
            (
                await db.execute(
                    select(PendingClassification.status, func.count())
                    .group_by(PendingClassification.status)
                )
            ).all()
        )

        # Heatmap aggregate rows.
        agg_rows = (
            await db.execute(
                select(HeatmapAggregate.topic, func.count())
                .join(School, School.id == HeatmapAggregate.source_id)
                .where(School.tenant_id == tenant_id)
                .group_by(HeatmapAggregate.topic)
            )
        ).all()

    print("\n" + "=" * 70)
    print(f"HEATMAP BACKFILL SUMMARY (tenant {tenant_id})")
    print("=" * 70)

    print("\nTop 10 schools by document count:")
    for name, cnt in school_doc_counts:
        print(f"  {cnt:5d}  {name}")

    print("\nPending classifications by status:")
    for status, cnt in sorted(pending_by_status.items()):
        print(f"  {status:12s} {cnt}")
    print(f"  {'total':12s} {pending_total}")

    print("\nHeatmap aggregate rows per topic:")
    for topic, cnt in sorted(agg_rows, key=lambda x: -x[1]):
        marker = " (in taxonomy)" if topic in TOPICS else " (orphan)"
        print(f"  {cnt:5d}  {topic}{marker}")

    total_agg = sum(c for _, c in agg_rows)
    print(f"\nTotal heatmap_aggregate rows: {total_agg}")
    print("=" * 70 + "\n")
    return {
        "top_schools": list(school_doc_counts),
        "pending_by_status": dict(pending_by_status),
        "agg_rows_per_topic": dict(agg_rows),
        "total_agg_rows": total_agg,
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


async def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Heatmap backfill orchestrator")
    parser.add_argument("--tenant-id", type=int, required=True, help="Tenant ID to backfill")
    parser.add_argument("--skip-scrape", action="store_true", help="Skip the scrape cycle (assume docs already ingested)")
    parser.add_argument("--submit-batch", action="store_true", help="Submit pending batch classification")
    parser.add_argument("--batch-id", type=str, default=None, help="Wait on a specific batch ID")
    parser.add_argument("--reconcile-only", action="store_true", help="Only run reconciliation + summary")
    parser.add_argument("--no-wait", action="store_true", help="Don't block on ingest/batch completion")
    args = parser.parse_args(argv)

    # Step 1: Verify charter mapping.
    await verify_charter_mapping(args.tenant_id)

    if args.reconcile_only:
        run_reconciliation(args.tenant_id)
        await print_summary(args.tenant_id)
        return 0

    # Step 2: Scrape (offline only via scripts/school_data/).
    if not args.skip_scrape:
        logger.warning(
            "[2/6] Automated scrape cycles were removed. Run scraping via "
            "scripts/school_data/, then pass --skip-scrape. Continuing to "
            "poll existing scraped media..."
        )
    else:
        logger.info("[2/6] Skipping scrape step (--skip-scrape)")

    # Step 3: Poll ingest progress.
    if not args.no_wait:
        await poll_ingest_progress(args.tenant_id)
    else:
        logger.info("[3/6] Skipping ingest poll (--no-wait)")

    # Step 4: Submit batch classification.
    if args.submit_batch:
        submit_batch()
    else:
        logger.info("[4/6] Skipping batch submit (use --submit-batch to trigger)")

    # Step 5: Wait for batch to be applied.
    if not args.no_wait and args.submit_batch:
        await wait_for_batch_applied(args.batch_id)
    else:
        logger.info("[5/6] Skipping batch wait")

    # Step 6: Reconcile + summary.
    if not args.no_wait:
        run_reconciliation(args.tenant_id)
    await print_summary(args.tenant_id)

    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
