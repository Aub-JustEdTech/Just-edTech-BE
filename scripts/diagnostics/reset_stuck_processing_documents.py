"""Recover documents orphaned at processing_status='processing'.

Background: a Celery chain stage can complete successfully and leave a
document at PROCESSING, but the chain fails to trigger its next stage (e.g.
the broker silently drops the continuation - see the Redis
maxmemory/allkeys-lru incident this script was written for). No exception is
ever raised, so `_mark_stage_failed` in app/tasks/document_pipeline.py never
runs and the row is stuck forever: no worker holds it, no queue holds it, and
it never becomes COMPLETED or FAILED on its own.

This script, for documents that have been stuck in 'processing' for longer
than --stale-hours:
  1. Deletes any existing chunk points (`documents` collection) and summary
     point (`summaries` collection) already written by the partial run, so
     the retry doesn't create duplicates on top of a partial success
     (vector_store.delete_document / delete_document_summary are otherwise
     never called before a reprocess - see the duplicate-points investigation
     for tenant 2/4).
  2. Resets the document to PENDING, clears error_message/chunk_count.
  3. Creates a new DocumentProcessingJob row and re-enqueues
     process_document_pipeline.

Defaults to --dry-run. Pass --apply to actually make changes.

Usage (inside the api container):
    docker exec just-edtech-api python scripts/diagnostics/reset_stuck_processing_documents.py --tenant-id 4 --dry-run
    docker exec just-edtech-api python scripts/diagnostics/reset_stuck_processing_documents.py --tenant-id 4 --apply
"""

import argparse
import asyncio
import sys

from sqlalchemy import select

from app.db.connector import AsyncSessionLocal
from app.models.documents import Document, ProcessingStatus
from app.models.processing_jobs import DocumentProcessingJob, JobStatus
from app.services.vector_store.factory import VectorStoreFactory


async def find_stuck(tenant_id: int, stale_hours: int) -> list[Document]:
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(Document)
            .where(
                Document.tenant_id == tenant_id,
                Document.processing_status == ProcessingStatus.PROCESSING,
                Document.updated_at < _now_minus_hours(stale_hours),
            )
            .order_by(Document.id.asc())
        )
        return list(result.scalars().all())


def _now_minus_hours(hours: int):
    from datetime import UTC, datetime, timedelta

    return datetime.now(UTC).replace(tzinfo=None) - timedelta(hours=hours)


async def reset_and_requeue(tenant_id: int, stale_hours: int, apply: bool) -> dict:
    docs = await find_stuck(tenant_id, stale_hours)
    stats = {"total": len(docs), "reset": 0, "chunks_deleted": 0, "summaries_deleted": 0}

    print("=" * 60)
    print("Reset stuck-processing documents")
    print(f"  tenant_id     : {tenant_id}")
    print(f"  stale_hours   : {stale_hours}")
    print(f"  mode          : {'APPLY' if apply else 'DRY RUN'}")
    print(f"  candidates    : {len(docs)}")
    print("=" * 60)

    if not docs:
        print("No stuck documents found.")
        return stats

    if not apply:
        for doc in docs[:20]:
            print(f"  [dry] doc={doc.id} chunk_count={doc.chunk_count} name={doc.name[:60]!r}")
        if len(docs) > 20:
            print(f"  ... and {len(docs) - 20} more")
        print("\nRe-run with --apply to actually reset and requeue these documents.")
        return stats

    vector_store = VectorStoreFactory.create()
    from app.tasks.document_pipeline import process_document_pipeline

    for doc in docs:
        try:
            deleted_chunks = await vector_store.delete_document(doc.doc_id, tenant_id)
            if deleted_chunks:
                stats["chunks_deleted"] += 1
        except Exception as exc:  # noqa: BLE001 - best-effort cleanup, log and continue
            print(f"  [warn] doc={doc.id} failed to delete existing chunks: {exc}")

        try:
            deleted_summary = await vector_store.delete_document_summary(doc.id, tenant_id)
            if deleted_summary:
                stats["summaries_deleted"] += 1
        except Exception as exc:  # noqa: BLE001
            print(f"  [warn] doc={doc.id} failed to delete existing summary: {exc}")

        async with AsyncSessionLocal() as db:
            doc_row = await db.get(Document, doc.id)
            if not doc_row:
                continue
            doc_row.processing_status = ProcessingStatus.PENDING
            doc_row.error_message = None
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

        process_document_pipeline.delay(doc.id, job_id)
        stats["reset"] += 1
        print(f"  [ok] doc={doc.id} job={job_id} requeued: {doc.name[:60]!r}")

    print("\nResults:")
    for k, v in stats.items():
        print(f"  {k:<18}: {v}")
    return stats


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tenant-id", type=int, required=True)
    parser.add_argument(
        "--stale-hours",
        type=int,
        default=1,
        help="Only touch documents whose updated_at is older than this many hours (default: 1)",
    )
    parser.add_argument("--apply", action="store_true", help="Actually reset and requeue (default is dry-run)")
    parser.add_argument("--dry-run", action="store_true", help="Explicit dry-run (default behavior)")
    args = parser.parse_args()

    try:
        asyncio.run(
            reset_and_requeue(
                tenant_id=args.tenant_id,
                stale_hours=args.stale_hours,
                apply=args.apply,
            )
        )
    except Exception as exc:
        print(f"\nReset failed: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
