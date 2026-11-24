"""Diagnostic: breakdown of `documents` rows for a tenant by processing
outcome, to see where documents are dropping out before chunking.

Read-only. Safe to run against production DB.

Usage (inside the api container):
    docker exec just-edtech-api python scripts/diagnostics/tenant_doc_status_breakdown.py --tenant-id 4
"""

import argparse
import asyncio
import sys
from collections import Counter

from sqlalchemy import select

from app.db.connector import AsyncSessionLocal
from app.models.documents import Document, ProcessingStatus


def categorize_error(error_message: str | None) -> str:
    if not error_message:
        return "(none)"
    msg = error_message.lower()
    if "skipped_year" in msg:
        return "skipped_year"
    if "skipped_duplicate" in msg or "duplicate" in msg:
        return "duplicate"
    if "no_transcript" in msg or "no transcript" in msg:
        return "no_transcript"
    if "too_long" in msg or "too long" in msg or "duration" in msg:
        return "media_too_long"
    if "rate limit" in msg or "ratelimit" in msg or "429" in msg:
        return "rate_limited"
    if "timeout" in msg or "timed out" in msg:
        return "timeout"
    if "embedding" in msg:
        return "embedding_error"
    if "chunk" in msg:
        return "chunking_error"
    if "qdrant" in msg or "vector" in msg:
        return "vector_store_error"
    return f"other: {error_message[:80]}"


async def main(tenant_id: int) -> None:
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(
                Document.processing_status,
                Document.chunk_count,
                Document.summary,
                Document.error_message,
                Document.source_type,
            ).where(Document.tenant_id == tenant_id)
        )
        rows = result.all()

    if not rows:
        print(f"No documents found for tenant_id={tenant_id}")
        return

    total = len(rows)
    status_counts = Counter(r.processing_status.value for r in rows)
    source_counts = Counter(r.source_type or "(none)" for r in rows)

    has_summary = sum(1 for r in rows if r.summary)
    has_chunks = sum(1 for r in rows if (r.chunk_count or 0) > 0)
    summary_no_chunks = sum(
        1 for r in rows if r.summary and (r.chunk_count or 0) == 0
    )
    chunks_no_summary = sum(
        1 for r in rows if not r.summary and (r.chunk_count or 0) > 0
    )
    completed_zero_chunks = sum(
        1
        for r in rows
        if r.processing_status == ProcessingStatus.COMPLETED
        and (r.chunk_count or 0) == 0
    )

    failed_rows = [r for r in rows if r.processing_status == ProcessingStatus.FAILED]
    failed_error_categories = Counter(categorize_error(r.error_message) for r in failed_rows)
    failed_with_summary = sum(1 for r in failed_rows if r.summary)

    print(f"=== Tenant {tenant_id}: Document pipeline breakdown ===")
    print(f"Total documents: {total}\n")

    print("-- By processing_status --")
    for status, count in status_counts.most_common():
        print(f"  {status:12s}: {count:6d}  ({count / total:.1%})")

    print("\n-- By source_type --")
    for source, count in source_counts.most_common():
        print(f"  {source:20s}: {count:6d}  ({count / total:.1%})")

    print("\n-- Summary vs chunk coverage --")
    print(f"  Docs with a summary (DB `summary` column set): {has_summary:6d}  ({has_summary / total:.1%})")
    print(f"  Docs with chunk_count > 0:                     {has_chunks:6d}  ({has_chunks / total:.1%})")
    print(f"  Docs with summary but chunk_count == 0 (ORPHAN CANDIDATES): {summary_no_chunks:6d}")
    print(f"  Docs with chunks but no summary (summarizer failed, non-fatal): {chunks_no_summary:6d}")
    print(f"  Docs COMPLETED but chunk_count == 0 (possible silent Qdrant failure): {completed_zero_chunks:6d}")

    print(f"\n-- FAILED documents breakdown (n={len(failed_rows)}) --")
    print(f"  FAILED docs that still have a summary written: {failed_with_summary:6d}")
    for category, count in failed_error_categories.most_common():
        print(f"  {category:30s}: {count:6d}  ({count / max(len(failed_rows), 1):.1%})")

    print(
        "\nNote: 'summary but chunk_count == 0' is the strongest signal for the "
        "documents-vs-summaries mismatch you observed in Qdrant. Cross-check the "
        "biggest error category above against app/tasks/document_pipeline.py "
        "stage 2.6 (year gate) and stage 4/5 (embedding/Qdrant fatal failures)."
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tenant-id", type=int, required=True)
    args = parser.parse_args()
    try:
        asyncio.run(main(args.tenant_id))
    except KeyboardInterrupt:
        sys.exit(130)
