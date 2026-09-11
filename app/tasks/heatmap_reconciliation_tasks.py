"""
Nightly reconciliation for heatmap_aggregate.

Recomputes the per-(school, topic) aggregate from Qdrant (the source of
truth for chunk-level classification) to catch drift caused by failed
set_payload calls, manual edits, or pipeline bugs.

Scheduled nightly at 3:30 AM UTC (between the daily token aggregation at
2 AM and the batch submit at 4 AM).

School resolution: chunk payloads carry `document_id` in the form
`school-{org_code}-{hash}` (set at ingest in school_scraper_tasks.py) but
do NOT carry a top-level `school_id` payload field. The earlier per-school
filter (`must_match={"school_id": school.id}`) therefore matched nothing.
This implementation scrolls once per topic, parses `org_code` from the
`document_id` prefix, and resolves it to a School row via `School.org_code`.
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

# Scroll page size for Qdrant. filter_chunks caps at 10000 per call and
# loops internally up to `limit`. A topic with very heavy coverage (e.g.
# parental_rights across the full corpus) can exceed 10k chunks; 50k is a
# safe ceiling for one topic in one tenant.
SCROLL_LIMIT = 50_000


@celery_app.task(name="reconcile_heatmap_aggregate", bind=True, max_retries=1)
def reconcile_heatmap_aggregate_task(self, tenant_id: int | None = None) -> dict:
    """
    Recompute heatmap_aggregate from Qdrant.

    For each topic, scroll Qdrant for classified chunks tagged with that
    topic, resolve the school per chunk from the `document_id` prefix,
    and upsert per-(school, topic) counts into heatmap_aggregate.

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


def _org_code_from_document_id(document_id: str | None) -> str | None:
    """Extract the school org_code from a `school-{org_code}-{hash}` doc id."""
    if not document_id:
        return None
    parts = document_id.split("-", 2)
    if len(parts) < 3 or not parts[1]:
        return None
    return parts[1]


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
            # Cache org_code -> School for this tenant once. Chunks resolve
            # their school via the `school-{org_code}-{hash}` document_id
            # prefix; the org_code maps 1:1 to School.org_code.
            schools = list(
                (
                    await db.execute(
                        select(School).where(School.tenant_id == tid)
                    )
                ).scalars().all()
            )
            org_to_school: dict[str, School] = {
                s.org_code: s for s in schools if s.org_code
            }

            rows = await _reconcile_tenant(
                db=db,
                vector_store=vector_store,
                tenant_id=tid,
                org_to_school=org_to_school,
            )
            total_rows += rows["rows_upserted"]
            total_schools += rows["schools_reconciled"]

        await db.commit()
        return {
            "tenants": len(tenant_ids),
            "schools_reconciled": total_schools,
            "rows_upserted": total_rows,
        }


async def _reconcile_tenant(
    *,
    db,
    vector_store,
    tenant_id: int,
    org_to_school: dict[str, School],
) -> dict:
    """Reconcile one tenant. Returns counts for this tenant."""
    # Aggregate per (school_id, topic) in memory. Each topic is one
    # Qdrant scroll; grouping happens locally. This is 8 scrolls (one per
    # topic) instead of the old 8*N-schools scrolls, and crucially it works
    # because it doesn't filter on the missing `school_id` payload field.
    #
    # Structure:
    #   school_topic_stats[(school_id, topic)] = {
    #       "chunk_count": int,
    #       "doc_ids": set[str],
    #       "meeting_dates": set[date],
    #       "last_meeting_date": date | None,
    #       "action_counts": dict[str, int],
    #   }
    school_topic_stats: dict[tuple[int, str], dict] = {}
    schools_seen: set[int] = set()
    chunks_scanned = 0
    chunks_unresolved = 0

    for topic in TOPICS:
        chunks = await vector_store.filter_chunks(
            tenant_id=tenant_id,
            must_match={"classified": True},
            must_match_any={"topics": [topic]},
            limit=SCROLL_LIMIT,
        )
        if not chunks:
            continue

        for ch in chunks:
            chunks_scanned += 1
            meta = ch.get("metadata", {})
            org_code = _org_code_from_document_id(meta.get("document_id"))
            school = org_to_school.get(org_code) if org_code else None
            if school is None:
                chunks_unresolved += 1
                continue

            schools_seen.add(school.id)
            key = (school.id, topic)
            stats = school_topic_stats.get(key)
            if stats is None:
                stats = {
                    "chunk_count": 0,
                    "doc_ids": set(),
                    "meeting_dates": set(),
                    "last_meeting_date": None,
                    "action_counts": defaultdict(int),
                }
                school_topic_stats[key] = stats

            stats["chunk_count"] += 1
            doc_id = meta.get("document_id")
            if doc_id:
                stats["doc_ids"].add(doc_id)
            md = meta.get("meeting_date")
            if md:
                try:
                    d = datetime.fromisoformat(str(md)).date()
                    stats["meeting_dates"].add(d)
                    if stats["last_meeting_date"] is None or d > stats["last_meeting_date"]:
                        stats["last_meeting_date"] = d
                except (ValueError, TypeError):
                    pass
            for action in meta.get("action_types", []) or []:
                stats["action_counts"][action] += 1

    if chunks_unresolved:
        logger.warning(
            f"reconcile tenant {tenant_id}: {chunks_unresolved}/{chunks_scanned} "
            f"chunks could not be resolved to a school (missing or unknown "
            f"org_code in document_id); they were skipped."
        )

    # Upsert each (school, topic) aggregate row.
    rows_upserted = 0
    for (school_id, topic), stats in school_topic_stats.items():
        existing = (
            await db.execute(
                select(HeatmapAggregate).where(
                    HeatmapAggregate.source_id == school_id,
                    HeatmapAggregate.topic == topic,
                )
            )
        ).scalar_one_or_none()
        action_counts = dict(stats["action_counts"])
        if existing is not None:
            existing.chunk_count = stats["chunk_count"]
            existing.doc_count = len(stats["doc_ids"])
            existing.meeting_count = len(stats["meeting_dates"])
            existing.last_meeting_date = stats["last_meeting_date"]
            existing.action_types = action_counts
        else:
            db.add(
                HeatmapAggregate(
                    source_id=school_id,
                    topic=topic,
                    chunk_count=stats["chunk_count"],
                    doc_count=len(stats["doc_ids"]),
                    meeting_count=len(stats["meeting_dates"]),
                    last_meeting_date=stats["last_meeting_date"],
                    action_types=action_counts,
                )
            )
        rows_upserted += 1

    return {
        "schools_reconciled": len(schools_seen),
        "rows_upserted": rows_upserted,
        "chunks_scanned": chunks_scanned,
        "chunks_unresolved": chunks_unresolved,
    }
