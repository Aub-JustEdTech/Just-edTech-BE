"""
Celery tasks for OpenAI Batch API chunk classification.

Two scheduled tasks:
  - submit_pending_batch_classification: daily at 4 AM UTC. Pulls all
    status='pending' rows from pending_classifications, builds a JSONL,
    uploads it to OpenAI's Batch API, and flips rows to 'submitted'.
  - poll_batch_classification: every 15 minutes. For every batch job in
    'submitted' or 'in_progress' state, refreshes status from OpenAI and,
    if completed, applies the results (writes to Qdrant + heatmap_aggregate).

These run on the default queue (not the scraping or documents queues) so
they don't compete with the heavy ingest path.
"""

import logging

from sqlalchemy import select

from app.celery_app import celery_app
from app.db.connector import AsyncSessionLocal
from app.models.batch_classification_job import BatchClassificationJob
from app.tasks.loop_utils import get_event_loop

logger = logging.getLogger(__name__)


@celery_app.task(
    name="submit_pending_batch_classification",
    bind=True,
    max_retries=1,
)
def submit_pending_batch_classification_task(self) -> dict:
    """
    Submit any pending chunk classifications to OpenAI's Batch API.

    Scheduled daily at 4 AM UTC. Can also be triggered manually for a
    backfill (see scripts/backfill/run_heatmap_backfill.py).
    """
    loop = get_event_loop()
    try:
        result = loop.run_until_complete(_submit_pending_batch_async())
        logger.info(f"submit_pending_batch_classification: {result}")
        return result
    except Exception as exc:
        logger.error(
            f"submit_pending_batch_classification failed: {exc}",
            exc_info=True,
        )
        raise self.retry(exc=exc, countdown=300) from exc


async def _submit_pending_batch_async() -> dict:
    from app.services.heatmap_ingest.batch_classifier import BatchClassifier

    async with AsyncSessionLocal() as db:
        classifier = BatchClassifier()
        job = await classifier.submit_pending_batch(db)
        if job is None:
            return {"submitted": False, "reason": "no pending chunks"}
        return {
            "submitted": True,
            "batch_id": job.batch_id,
            "chunk_count": job.chunk_count,
        }


@celery_app.task(
    name="poll_batch_classification",
    bind=True,
    max_retries=1,
)
def poll_batch_classification_task(self) -> dict:
    """
    Poll all in-flight batch jobs and apply results for any that completed.

    Scheduled every 15 minutes. A job is 'in-flight' if its status is
    'submitted' or 'in_progress' (the OpenAI Batch API lifecycle states
    before completion/failure/expiry).
    """
    loop = get_event_loop()
    try:
        result = loop.run_until_complete(_poll_batches_async())
        logger.info(f"poll_batch_classification: {result}")
        return result
    except Exception as exc:
        logger.error(
            f"poll_batch_classification failed: {exc}", exc_info=True
        )
        raise self.retry(exc=exc, countdown=120) from exc


async def _poll_batches_async() -> dict:
    from app.services.heatmap_ingest.batch_classifier import BatchClassifier

    async with AsyncSessionLocal() as db:
        # Find all in-flight batches. We poll each one; if it's completed,
        # poll_batch (called inside apply_batch_results) will flip it to
        # 'completed' and then apply_batch_results applies the results.
        in_flight = (
            await db.execute(
                select(BatchClassificationJob).where(
                    BatchClassificationJob.status.in_(
                        ["submitted", "in_progress", "validating", "finalizing"]
                    )
                )
            )
        ).scalars().all()

        if not in_flight:
            return {"polled": 0, "applied": 0, "failed": 0}

        classifier = BatchClassifier()
        applied = 0
        failed = 0
        for job in in_flight:
            try:
                # Refresh status first.
                refreshed = await classifier.poll_batch(db, job.batch_id)
                if refreshed.status == "completed":
                    stats = await classifier.apply_batch_results(db, job.batch_id)
                    applied += stats.get("applied", 0)
                    failed += stats.get("failed", 0)
                elif refreshed.status in ("failed", "expired", "cancelled"):
                    failed += 1
                    logger.warning(
                        f"Batch {job.batch_id} ended in status "
                        f"{refreshed.status}: {refreshed.error_message}"
                    )
            except Exception as exc:  # noqa: BLE001
                logger.error(
                    f"Failed to process batch {job.batch_id}: {exc}",
                    exc_info=True,
                )
                failed += 1

        return {
            "polled": len(in_flight),
            "applied": applied,
            "failed": failed,
        }


@celery_app.task(name="apply_batch_results", bind=True, max_retries=1)
def apply_batch_results_task(self, batch_id: str) -> dict:
    """
    Manually apply results for a specific completed batch.

    Useful for the backfill script (Phase 8) where we want to block on a
    specific batch rather than wait for the 15-min poller.
    """
    loop = get_event_loop()
    try:
        result = loop.run_until_complete(_apply_batch_async(batch_id))
        logger.info(f"apply_batch_results({batch_id}): {result}")
        return result
    except Exception as exc:
        logger.error(
            f"apply_batch_results failed for {batch_id}: {exc}",
            exc_info=True,
        )
        raise self.retry(exc=exc, countdown=120) from exc


async def _apply_batch_async(batch_id: str) -> dict:
    from app.services.heatmap_ingest.batch_classifier import BatchClassifier

    async with AsyncSessionLocal() as db:
        classifier = BatchClassifier()
        return await classifier.apply_batch_results(db, batch_id)
