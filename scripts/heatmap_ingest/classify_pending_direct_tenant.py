"""Classify pending_classifications for one tenant via direct chat API.

Uses the OpenRouter/direct path in BatchClassifier (no OpenAI Batch API).
Scoped by tenant so a manual run does not pull other tenants' pending rows.

Usage:
  POSTGRES_SERVER=localhost QDRANT_URL=http://localhost:6343 \\
    poetry run python scripts/heatmap_ingest/classify_pending_direct_tenant.py \\
      --tenant-id 5
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.connector import AsyncSessionLocal
from app.models.batch_classification_job import BatchClassificationJob
from app.models.documents import Document
from app.models.pending_classification import PendingClassification
from app.services.heatmap_ingest.batch_classifier import BatchClassifier
from app.services.heatmap_ingest.prompt import build_batch_request_line
from app.services.heatmap_ingest.taxonomy import ChunkClassification
from app.services.llm.client import normalize_model_name
from app.core.config import settings

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger("classify_pending_direct_tenant")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


async def classify_tenant(
    db: AsyncSession,
    *,
    tenant_id: int,
    include_failed: bool,
    limit: int | None,
) -> dict:
    statuses = ["pending"]
    if include_failed:
        statuses.append("failed")

    q = (
        select(PendingClassification)
        .join(Document, Document.id == PendingClassification.document_id)
        .where(
            Document.tenant_id == tenant_id,
            PendingClassification.status.in_(statuses),
        )
        .order_by(PendingClassification.id)
    )
    if limit is not None:
        q = q.limit(limit)

    pending_rows = (await db.execute(q)).scalars().all()
    if not pending_rows:
        logger.info("No rows to classify for tenant %s", tenant_id)
        return {"applied": 0, "failed": 0, "total": 0}

    # Failed rows must be pending-shaped for this path.
    for row in pending_rows:
        if row.status == "failed":
            row.status = "pending"
            row.error_message = None
            row.batch_id = None

    concurrency = int(getattr(settings, "OPENROUTER_BATCH_CONCURRENCY", 10))
    classifier = BatchClassifier()
    batch_id = f"direct-t{tenant_id}-{_utcnow().strftime('%Y%m%d_%H%M%S')}"
    logger.info(
        "Classifying %s chunks for tenant %s via direct API (concurrency=%s)",
        len(pending_rows),
        tenant_id,
        concurrency,
    )

    from app.services.vector_store.factory import VectorStoreFactory, VectorStoreType

    vector_store = VectorStoreFactory.create(
        VectorStoreType(settings.VECTOR_STORE_TYPE)
    )
    doc_state_cache: dict[int, str | None] = {}
    per_doc_classifications: dict[
        int, list[tuple[PendingClassification, ChunkClassification]]
    ] = {}
    stats = {"applied": 0, "failed": 0}
    semaphore = asyncio.Semaphore(concurrency)
    done = 0
    total = len(pending_rows)

    async def _classify_row(row: PendingClassification) -> None:
        nonlocal done
        async with semaphore:
            if row.document_id not in doc_state_cache:
                doc_state_cache[row.document_id] = await classifier._state_for_doc(
                    db, row.document_id
                )
            request = build_batch_request_line(
                custom_id=str(row.id),
                chunk_text=row.chunk_text,
                entity_type=row.entity_type,
                meeting_date=(
                    row.meeting_date.isoformat() if row.meeting_date else None
                ),
                state=doc_state_cache[row.document_id],
                model=classifier._model,
            )
            body = dict(request["body"])
            body["model"] = normalize_model_name(body["model"])
            try:
                response = await classifier._client.chat.completions.create(**body)
                content = response.choices[0].message.content or "{}"
                classification = ChunkClassification.model_validate(json.loads(content))
            except Exception as exc:  # noqa: BLE001
                row.status = "failed"
                row.error_message = str(exc)[:1000]
                stats["failed"] += 1
                done += 1
                if done % 25 == 0 or done == total:
                    logger.info("progress %s/%s (applied=%s failed=%s)", done, total, stats["applied"], stats["failed"])
                return

            try:
                if hasattr(vector_store, "update_metadata"):
                    await classifier._update_metadata_with_retry(
                        vector_store,
                        chunk_ids=[row.qdrant_point_id],
                        metadata=classifier._build_payload_metadata(
                            classification, row_entity_type=row.entity_type
                        ),
                        tenant_id=tenant_id,
                    )
            except Exception as exc:  # noqa: BLE001
                row.status = "failed"
                row.error_message = f"qdrant set_payload: {exc}"[:1000]
                stats["failed"] += 1
                done += 1
                if done % 25 == 0 or done == total:
                    logger.info("progress %s/%s (applied=%s failed=%s)", done, total, stats["applied"], stats["failed"])
                return

            per_doc_classifications.setdefault(row.document_id, []).append(
                (row, classification)
            )
            row.status = "applied"
            row.batch_id = batch_id
            row.error_message = None
            stats["applied"] += 1
            done += 1
            if done % 25 == 0 or done == total:
                logger.info(
                    "progress %s/%s (applied=%s failed=%s)",
                    done,
                    total,
                    stats["applied"],
                    stats["failed"],
                )

    await asyncio.gather(*[_classify_row(row) for row in pending_rows])
    await classifier._upsert_heatmap_aggregate(db, per_doc_classifications)

    job = BatchClassificationJob(
        batch_id=batch_id,
        input_jsonl_s3_key=f"local://{batch_id}",
        chunk_count=len(pending_rows),
        status="applied",
        submitted_at=_utcnow(),
        applied_at=_utcnow(),
    )
    db.add(job)
    await db.commit()
    logger.info(
        "Done %s: applied=%s failed=%s total=%s",
        batch_id,
        stats["applied"],
        stats["failed"],
        total,
    )
    return {**stats, "total": total, "batch_id": batch_id}


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tenant-id", type=int, required=True)
    parser.add_argument(
        "--include-failed",
        action="store_true",
        default=True,
        help="Also retry status=failed rows (default: true)",
    )
    parser.add_argument(
        "--no-include-failed",
        action="store_false",
        dest="include_failed",
    )
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()

    async with AsyncSessionLocal() as db:
        result = await classify_tenant(
            db,
            tenant_id=args.tenant_id,
            include_failed=args.include_failed,
            limit=args.limit,
        )
    print(result)
    return 0 if result.get("failed", 0) == 0 else 2


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
