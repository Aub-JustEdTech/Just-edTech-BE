#!/usr/bin/env python3
"""
Re-queue the document pipeline for failed school_scraper documents.

Use when documents were downloaded to S3 but the processing pipeline failed
(e.g. the document_type extension bug, or empty text before OCR was enabled).
Does not re-download from district sites.

Usage:
    python scripts/school_data/reprocess_failed_documents.py --dry-run
    python scripts/school_data/reprocess_failed_documents.py --tenant-id 2
    python scripts/school_data/reprocess_failed_documents.py --tenant-id 2 --only-no-text
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from sqlalchemy import select

from app.core.config import settings
from app.db.connector import AsyncSessionLocal
from app.models.documents import Document, ProcessingStatus
from app.models.processing_jobs import DocumentProcessingJob, JobStatus

NO_TEXT_ERROR = "No text extracted from document"


def _normalize_document_extension(ext: str | None) -> str:
    if not ext:
        return ".txt"
    return ext if ext.startswith(".") else f".{ext}"


async def reprocess_failed(
    *,
    tenant_id: int,
    dry_run: bool,
    only_no_text: bool,
) -> dict:
    print("=" * 60)
    print("School Scraper Document Reprocess")
    print(f"  tenant_id     : {tenant_id}")
    print(f"  dry_run       : {dry_run}")
    print(f"  only_no_text  : {only_no_text}")
    print("=" * 60)

    async with AsyncSessionLocal() as db:
        docs = list(
            (
                await db.execute(
                    select(Document)
                    .where(
                        Document.tenant_id == tenant_id,
                        Document.source_type == "school_scraper",
                        Document.processing_status == ProcessingStatus.FAILED,
                    )
                    .order_by(Document.id.asc())
                )
            )
            .scalars()
            .all()
        )

    if only_no_text:
        docs = [d for d in docs if d.error_message and NO_TEXT_ERROR in d.error_message]

    stats = {"total": len(docs), "enqueued": 0, "dry_run": 0}

    if not docs:
        print("No matching failed school_scraper documents found.")
        return stats

    from app.tasks.document_pipeline import process_document_pipeline

    for doc in docs:
        normalized = _normalize_document_extension(doc.document_type)
        if dry_run:
            stats["dry_run"] += 1
            print(
                f"  [dry]  doc={doc.id} {doc.name[:50]} "
                f"type={doc.document_type!r} -> {normalized!r}"
            )
            continue

        async with AsyncSessionLocal() as db:
            doc_row = await db.get(Document, doc.id)
            if not doc_row:
                continue
            doc_row.document_type = normalized
            doc_row.processing_status = ProcessingStatus.PENDING
            doc_row.error_message = None

            job = DocumentProcessingJob(
                document_id=doc_row.id,
                status=JobStatus.PENDING,
                processor_type=normalized,
            )
            db.add(job)
            await db.flush()
            await db.commit()
            job_id = job.id

        process_document_pipeline.delay(doc.id, job_id)
        stats["enqueued"] += 1
        print(f"  [ok]   doc={doc.id} job={job_id} {doc.name[:50]}")

    print("\nReprocess results:")
    for k, v in stats.items():
        print(f"  {k:<12}: {v}")
    return stats


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Re-queue failed school_scraper documents through the pipeline."
    )
    parser.add_argument(
        "--tenant-id",
        type=int,
        default=settings.DEFAULT_TENANT_ID,
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--only-no-text",
        action="store_true",
        help=(
            "Only re-queue docs that failed with "
            f"'{NO_TEXT_ERROR}' (OCR candidates)."
        ),
    )
    args = parser.parse_args()

    try:
        asyncio.run(
            reprocess_failed(
                tenant_id=args.tenant_id,
                dry_run=args.dry_run,
                only_no_text=args.only_no_text,
            )
        )
    except Exception as exc:
        print(f"\nReprocess failed: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
