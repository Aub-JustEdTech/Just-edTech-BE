"""
Hourly reconciliation for documents stuck at PROCESSING or PENDING.

A Celery chain stage can complete successfully but fail to enqueue its
continuation (the failure mode behind the Redis ``maxmemory``/``allkeys-lru``
incident: the broker silently evicted the in-flight continuation message, no
exception was ever raised, and ``_mark_stage_failed`` never ran). A second,
distinct mode is the ``OutOfMemoryError`` path under ``noeviction``: a PENDING
document's first stage-1 message was rejected by Redis and lost outright, so
restarting workers does not help — there is nothing left in the queue for them
to consume.

This task finds documents whose ``processing_status`` is still PROCESSING or
PENDING past a staleness threshold (default 45 min) and re-enqueues them through
``process_document_pipeline``. It is intentionally tenant-agnostic: it scans
every tenant in turn so no district is silently orphaned.

Scheduled hourly (overlap-safe via ``expires``). The ``stale_minutes`` override
is exposed for manual invocation when you want a tighter window than the
default beat schedule uses.

Follows the established reconciliation-task pattern (sync Celery wrapper ->
``get_event_loop().run_until_complete(async_impl)`` -> structured return dict),
mirroring ``app/tasks/heatmap_reconciliation_tasks.py``.
"""

import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.celery_app import celery_app
from app.core.config import settings
from app.db.connector import AsyncSessionLocal
from app.models.documents import Document, ProcessingStatus
from app.models.processing_jobs import DocumentProcessingJob, JobStatus
from app.services.vector_store.factory import (
    VectorStoreFactory,
    VectorStoreType,
)
from app.tasks.loop_utils import get_event_loop

logger = logging.getLogger(__name__)

# Documents stuck for longer than this (regardless of status) are considered
# orphaned. 45 min comfortably exceeds the longest single-stage runtime
# (summarization ~30 s, chunking/embedding ~2-5 min, transcription lives on a
# separate scraping queue with its own 6000s soft limit) without being so
# short that an in-flight doc gets re-enqueued on top of itself.
DEFAULT_STALE_MINUTES = 45


@celery_app.task(name="reconcile_stuck_documents", bind=True, max_retries=1)
def reconcile_stuck_documents_task(
    self, stale_minutes: int = DEFAULT_STALE_MINUTES
) -> dict:
    """
    Find and re-enqueue documents stuck at PROCESSING/PENDING.

    Args:
        stale_minutes: Only touch documents whose ``updated_at`` is older
            than this many minutes. Defaults to 45.

    Returns:
        Summary dict (candidates, reset, chunks_deleted, summaries_deleted,
        tenants_scanned).
    """
    loop = get_event_loop()
    try:
        result = loop.run_until_complete(_reconcile_async(stale_minutes))
        logger.info(f"reconcile_stuck_documents: {result}")
        return result
    except Exception as exc:
        logger.error(
            f"reconcile_stuck_documents failed: {exc}", exc_info=True
        )
        raise self.retry(exc=exc, countdown=600) from exc


async def _reconcile_async(stale_minutes: int) -> dict:
    cutoff = datetime.now(UTC).replace(tzinfo=None) - timedelta(
        minutes=stale_minutes
    )

    async with AsyncSessionLocal() as db:
        # Tenants with potentially orphaned docs. Scanning per-tenant lets
        # each re-enqueue land on the right Qdrant collection without a
        # cross-tenant fan-out in a single transaction.
        tenant_ids = list(
            (
                await db.execute(
                    select(Document.tenant_id)
                    .where(
                        Document.processing_status.in_(
                            [ProcessingStatus.PROCESSING, ProcessingStatus.PENDING]
                        ),
                        Document.updated_at < cutoff,
                    )
                    .distinct()
                )
            ).scalars().all()
        )

        if not tenant_ids:
            return {
                "tenants_scanned": 0,
                "candidates": 0,
                "reset": 0,
                "chunks_deleted": 0,
                "summaries_deleted": 0,
            }

        total_candidates = 0
        total_reset = 0
        total_chunks_deleted = 0
        total_summaries_deleted = 0

        for tenant_id in tenant_ids:
            stats = await _reconcile_tenant(
                tenant_id=tenant_id, cutoff=cutoff
            )
            total_candidates += stats["candidates"]
            total_reset += stats["reset"]
            total_chunks_deleted += stats["chunks_deleted"]
            total_summaries_deleted += stats["summaries_deleted"]

        return {
            "tenants_scanned": len(tenant_ids),
            "candidates": total_candidates,
            "reset": total_reset,
            "chunks_deleted": total_chunks_deleted,
            "summaries_deleted": total_summaries_deleted,
        }


async def _reconcile_tenant(
    *, tenant_id: int, cutoff
) -> dict:
    """Reset and re-enqueue all stuck docs for one tenant."""
    stats = {
        "candidates": 0,
        "reset": 0,
        "chunks_deleted": 0,
        "summaries_deleted": 0,
    }

    async with AsyncSessionLocal() as db:
        docs = list(
            (
                await db.execute(
                    select(Document)
                    .where(
                        Document.tenant_id == tenant_id,
                        Document.processing_status.in_(
                            [ProcessingStatus.PROCESSING, ProcessingStatus.PENDING]
                        ),
                        Document.updated_at < cutoff,
                    )
                    .order_by(Document.id.asc())
                )
            ).scalars().all()
        )

        stats["candidates"] = len(docs)
        if not docs:
            return stats

    vector_store = VectorStoreFactory.create(
        VectorStoreType(settings.VECTOR_STORE_TYPE)
    )

    # Imported here to avoid the tasks -> models import cycle noted in
    # CLAUDE.md (the same pattern used by the manual reset script and
    # ingest_scraped_media).
    from app.tasks.document_pipeline import process_document_pipeline

    for doc in docs:
        # Delete any partial Qdrant state left by an earlier aborted run.
        # Without this the re-enqueue produces duplicate points (Qdrant
        # upserts by fresh random point-id; see the delete-before-recreate
        # fix in _step5_store_async).
        try:
            if await vector_store.delete_document(doc.doc_id, tenant_id):
                stats["chunks_deleted"] += 1
        except Exception as exc:  # noqa: BLE001 - best-effort, log and continue
            logger.warning(
                f"[Doc {doc.id}] reconcile: failed to delete existing chunks: {exc}"
            )

        if hasattr(vector_store, "delete_document_summary"):
            try:
                if await vector_store.delete_document_summary(
                    doc.id, tenant_id
                ):
                    stats["summaries_deleted"] += 1
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    f"[Doc {doc.id}] reconcile: failed to delete existing summary: {exc}"
                )

        async with AsyncSessionLocal() as db:
            doc_row = await db.get(Document, doc.id)
            if not doc_row:
                continue
            doc_row.processing_status = ProcessingStatus.PENDING
            # Empty string, not None: crud/schools.update_scraped_media skips
            # None values (see CLAUDE.md), and the same convention applies
            # here so a future retry clears the prior error visibly.
            doc_row.error_message = ""
            doc_row.chunk_count = 0

            job = DocumentProcessingJob(
                document_id=doc_row.id,
                status=JobStatus.PENDING,
                processor_type=doc_row.document_type,
            )
            db.add(job)
            await db.flush()
            await db.commit()
            job_id = job.id

        # Enqueue only AFTER the DB commit — otherwise the worker's
        # db.get(...) returns None and silently drops the item (CLAUDE.md).
        process_document_pipeline.delay(doc.id, job_id)
        stats["reset"] += 1
        logger.info(
            f"[Doc {doc.id}] reconcile: re-enqueued (job={job_id}, "
            f"tenant={tenant_id}) status was {doc.processing_status.value}"
        )

    return stats
