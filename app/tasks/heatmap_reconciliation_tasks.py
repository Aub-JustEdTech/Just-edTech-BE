"""
Nightly reconciliation for heatmap_aggregate.

Recomputes the per-(school, topic) aggregate from Qdrant (the source of
truth for chunk-level classification) to catch drift caused by failed
set_payload calls, manual edits, or pipeline bugs.

Scheduled nightly at 3:30 AM UTC (between the daily token aggregation at
2 AM and the batch submit at 4 AM).
"""

import logging
from collections import defaultdict
from datetime import date, datetime

from sqlalchemy import select

from app.celery_app import celery_app
from app.core.config import settings
from app.db.connector import AsyncSessionLocal
from app.models.heatmap_aggregate import HeatmapAggregate
from app.models.school import School
from app.services.heatmap_ingest.taxonomy import TOPICS
from app.tasks.loop_utils import get_event_loop

logger = logging.getLogger(__name__)


@celery_app.task(name="reconcile_heatmap_aggregate", bind=True, max_retries=1)
def reconcile_heatmap_aggregate_task(self, tenant_id: int | None = None) -> dict:
    """
    Recompute heatmap_aggregate from Qdrant.

    For each school with classified chunks, scroll Qdrant for chunks
    tagged with each topic, count chunks/docs/meetings/actions, and
    upsert into heatmap_aggregate (replacing any stale rows).

    Pass `tenant_id` to scope the reconciliation; omit to reconcile all
    tenants that have school_scraper docs.
    """
    loop = get_event_loop()
    try:
        result = loop.run_until_complete(_reconcile_async(tenant_id))
        logger.info(f"reconcile_heatmap_aggregate: {result}")
        return result
    except Exception as exc:
        logger.error(
            f"reconcile_heatmap_aggregate failed: {exc}", exc_info=True
        )
        raise self.retry(exc=exc, countdown=600) from exc


async def _reconcile_async(tenant_id: int | None) -> dict:
    from app.services.vector_store.factory import (
        VectorStoreFactory,
        VectorStoreType,
    )

    async with AsyncSessionLocal() as db:
        # Find all tenants that have school_scraper docs (or use the
        # explicit tenant_id).
        from app.models.documents import Document

        if tenant_id is not None:
            tenant_ids = [tenant_id]
        else:
            tenant_ids = list(
                (
                    await db.execute(
                        select(Document.tenant_id)
                        .where(Document.source_type == "school_scraper")
                        .distinct()
                    )
                ).scalars().all()
            )

        if not tenant_ids:
            return {"tenants": 0, "schools_reconciled": 0, "rows_upserted": 0}

        vector_store = VectorStoreFactory.create(
            VectorStoreType(settings.VECTOR_STORE_TYPE)
        )
        if not hasattr(vector_store, "filter_chunks"):
            logger.warning(
                "Vector store does not support filter_chunks; skipping reconciliation"
            )
            return {"tenants": len(tenant_ids), "schools_reconciled": 0, "rows_upserted": 0, "skipped": True}

        total_schools = 0
        total_rows = 0

        for tid in tenant_ids:
            # Find all schools for this tenant.
            schools = list(
                (
                    await db.execute(
                        select(School).where(School.tenant_id == tid)
                    )
                ).scalars().all()
            )
            for school in schools:
                rows = await _reconcile_school(
                    db=db,
                    vector_store=vector_store,
                    tenant_id=tid,
                    school=school,
                )
                total_rows += rows
                if rows:
                    total_schools += 1

        await db.commit()
        return {
            "tenants": len(tenant_ids),
            "schools_reconciled": total_schools,
            "rows_upserted": total_rows,
        }


async def _reconcile_school(
    *,
    db,
    vector_store,
    tenant_id: int,
    school: School,
) -> int:
    """Reconcile one school. Returns the number of topic rows upserted."""
    rows_upserted = 0

    for topic in TOPICS:
        chunks = await vector_store.filter_chunks(
            tenant_id=tenant_id,
            must_match={"school_id": school.id, "classified": True},
            must_match_any={"topics": [topic]},
            limit=10000,
        )
        if not chunks:
            continue

        # Aggregate from the chunk payloads.
        chunk_count = len(chunks)
        doc_ids: set[str] = set()
        meeting_dates: set[date] = set()
        last_meeting_date: date | None = None
        action_counts: dict[str, int] = defaultdict(int)

        for ch in chunks:
            meta = ch.get("metadata", {})
            if meta.get("document_id"):
                doc_ids.add(meta["document_id"])
            md = meta.get("meeting_date")
            if md:
                try:
                    d = datetime.fromisoformat(str(md)).date()
                    meeting_dates.add(d)
                    if last_meeting_date is None or d > last_meeting_date:
                        last_meeting_date = d
                except (ValueError, TypeError):
                    pass
            for action in meta.get("action_types", []) or []:
                action_counts[action] += 1

        # Upsert (delete + insert to handle staleness cleanly).
        existing = (
            await db.execute(
                select(HeatmapAggregate).where(
                    HeatmapAggregate.source_id == school.id,
                    HeatmapAggregate.topic == topic,
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            existing.chunk_count = chunk_count
            existing.doc_count = len(doc_ids)
            existing.meeting_count = len(meeting_dates)
            existing.last_meeting_date = last_meeting_date
            existing.action_types = dict(action_counts)
        else:
            db.add(
                HeatmapAggregate(
                    source_id=school.id,
                    topic=topic,
                    chunk_count=chunk_count,
                    doc_count=len(doc_ids),
                    meeting_count=len(meeting_dates),
                    last_meeting_date=last_meeting_date,
                    action_types=dict(action_counts),
                )
            )
        rows_upserted += 1

    return rows_upserted
