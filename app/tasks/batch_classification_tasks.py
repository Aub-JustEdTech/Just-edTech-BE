"""
Celery tasks for OpenAI Batch API chunk classification.

  - submit_pending_batch_classification: daily at 4 AM UTC (plus after each
    50-school sweep wave). Pulls status='pending' rows, submits to OpenAI
    Batch API, then arms the poller.
  - poll_batch_classification: NOT on beat_schedule. Armed only after a
    submit (or when it finds jobs still in flight and self-reschedules).
    Avoids hitting OpenAI every 15 minutes when nothing is pending.

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

# First OpenAI status check after submit, and between checks while a batch
# is still validating / in_progress / finalizing. Idle = no polls at all.
_POLL_COUNTDOWN_SECONDS = 15 * 60

_IN_FLIGHT_STATUSES = ("submitted", "in_progress", "validating", "finalizing")


@celery_app.task(
    name="submit_pending_batch_classification",
    bind=True,
    max_retries=1,
)
def submit_pending_batch_classification_task(self) -> dict:
    """
    Submit any pending chunk classifications to OpenAI's Batch API.

    Scheduled daily at 4 AM UTC. Can also be triggered manually for a
    backfill (see scripts/backfill/run_heatmap_backfill.py), or chained
    from each sweep_school_media wave.

    After a successful submit (or when in-flight jobs already exist), arms
    poll_batch_classification so we only hit OpenAI while work is pending.
    """
    loop = get_event_loop()
    try:
        result = loop.run_until_complete(_submit_pending_batch_async())
        logger.info(f"submit_pending_batch_classification: {result}")
        # Arm poller only when we actually submitted. The poller then
        # self-reschedules while jobs stay in flight. Avoids stacking a
        # new poll countdown on every empty sweep-wave submit kick.
        # Orphan recovery: next successful submit (or manual
        # poll_batch_classification.delay()) re-arms.
        if result.get("submitted"):
            poll_batch_classification_task.apply_async(
                countdown=_POLL_COUNTDOWN_SECONDS
            )
            logger.info(
                "Armed poll_batch_classification in %ss",
                _POLL_COUNTDOWN_SECONDS,
            )
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
        in_flight = await _count_in_flight(db)
        if job is None:
            return {
                "submitted": False,
                "reason": "no pending chunks",
                "in_flight": in_flight,
            }
        return {
            "submitted": True,
            "batch_id": job.batch_id,
            "chunk_count": job.chunk_count,
            "in_flight": in_flight,
        }


@celery_app.task(
    name="poll_batch_classification",
    bind=True,
    max_retries=1,
)
def poll_batch_classification_task(self) -> dict:
    """
    Poll all in-flight batch jobs and apply results for any that completed.

    Not on beat_schedule -- armed by submit_pending_batch_classification
    (and self-reschedules while jobs remain in flight). Stops when idle so
    we do not poll OpenAI on an empty queue.
    """
    loop = get_event_loop()
    try:
        result = loop.run_until_complete(_poll_batches_async())
        logger.info(f"poll_batch_classification: {result}")
        if result.get("still_in_flight", 0) > 0:
            poll_batch_classification_task.apply_async(
                countdown=_POLL_COUNTDOWN_SECONDS
            )
            logger.info(
                "Batch still in flight (%s); re-arming poll in %ss",
                result["still_in_flight"],
                _POLL_COUNTDOWN_SECONDS,
            )
        return result
    except Exception as exc:
        logger.error(
            f"poll_batch_classification failed: {exc}", exc_info=True
        )
        raise self.retry(exc=exc, countdown=120) from exc


async def _count_in_flight(db) -> int:
    result = await db.execute(
        select(BatchClassificationJob.id).where(
            BatchClassificationJob.status.in_(_IN_FLIGHT_STATUSES)
        )
    )
    return len(result.scalars().all())


async def _poll_batches_async() -> dict:
    from app.services.heatmap_ingest.batch_classifier import BatchClassifier

    async with AsyncSessionLocal() as db:
        # Find all in-flight batches. We poll each one; if it's completed,
        # poll_batch (called inside apply_batch_results) will flip it to
        # 'completed' and then apply_batch_results applies the results.
        in_flight = (
            await db.execute(
                select(BatchClassificationJob).where(
                    BatchClassificationJob.status.in_(_IN_FLIGHT_STATUSES)
                )
            )
        ).scalars().all()

        if not in_flight:
            return {
                "polled": 0,
                "applied": 0,
                "failed": 0,
                "still_in_flight": 0,
            }

        classifier = BatchClassifier()
        applied = 0
        failed = 0
        still_in_flight = 0
        for job in in_flight:
            try:
                # Refresh status first.
                refreshed = await classifier.poll_batch(db, job.batch_id)
                if refreshed.status == "completed":
                    stats = await classifier.apply_batch_results(db, job.batch_id)
                    applied += stats.get("applied", 0)
                    failed += stats.get("failed", 0)
                elif refreshed.status in _IN_FLIGHT_STATUSES:
                    still_in_flight += 1
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
                # Keep trying -- treat as still in flight so the poller
                # re-arms rather than abandoning the job after a transient.
                still_in_flight += 1

        return {
            "polled": len(in_flight),
            "applied": applied,
            "failed": failed,
            "still_in_flight": still_in_flight,
        }


@celery_app.task(name="apply_batch_results", bind=True, max_retries=1)
def apply_batch_results_task(self, batch_id: str) -> dict:
    """
    Manually apply results for a specific completed batch.

    Useful for the backfill script (Phase 8) where we want to block on a
    specific batch rather than wait for the post-submit poller.
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
