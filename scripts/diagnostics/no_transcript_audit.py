"""Diagnostic: count scraped_media with no usable extracted text.

Covers the empty-content terminal statuses that never create a Document
(so they never show up in documents.* audits):

  - no_transcript  — no captions / empty transcript / YouTube ingest disabled
  - no_audio        — file has no audio stream (decorative template videos)
  - skipped_silence / skipped_no_audio  — legacy labels if any remain

Also reports documents that *did* get created but have zero extracted
chunks (chunk_count == 0), which is a different "no data" failure mode.

Read-only. Safe to run against production.

Usage (on the prod host):
    # All tenants
    docker exec just-edtech-api python scripts/diagnostics/no_transcript_audit.py

    # One tenant
    docker exec just-edtech-api python scripts/diagnostics/no_transcript_audit.py --tenant-id 4

    # Sample rows for the re-ingest plan
    docker exec just-edtech-api python scripts/diagnostics/no_transcript_audit.py \\
        --tenant-id 4 --samples 20
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections import Counter

from sqlalchemy import func, select

from app.db.connector import AsyncSessionLocal
from app.models.documents import Document, ProcessingStatus
from app.models.school import ScrapedMedia

# Statuses that mean "we got the media but extracted no usable text"
# and therefore never linked a Document.
EMPTY_EXTRACTION_STATUSES = (
    "no_transcript",
    "no_audio",
    "skipped_no_audio",
    "skipped_silence",
)


def _bucket_error(msg: str | None) -> str:
    if not msg:
        return "(none)"
    m = msg.lower()
    if "youtube ingestion disabled" in m:
        return "youtube_ingest_disabled"
    if "transcript was empty" in m or "empty transcript" in m:
        return "empty_transcript"
    if "no speech" in m:
        return "no_speech_detected"
    if "no caption" in m or "captions" in m:
        return "no_captions"
    if "no audio" in m or "no audio stream" in m:
        return "no_audio_stream"
    if "disabled" in m:
        return "feature_disabled"
    return f"other: {msg[:90]}"


async def audit(tenant_id: int | None, samples: int) -> None:
    async with AsyncSessionLocal() as db:
        tenant_filter = ()
        if tenant_id is not None:
            tenant_filter = (ScrapedMedia.tenant_id == tenant_id,)

        # --- Full status histogram (context) ---
        status_rows = (
            await db.execute(
                select(ScrapedMedia.status, func.count())
                .where(*tenant_filter)
                .group_by(ScrapedMedia.status)
                .order_by(func.count().desc())
            )
        ).all()
        media_total = sum(c for _, c in status_rows)

        # --- Empty-extraction statuses ---
        empty_rows = (
            await db.execute(
                select(
                    ScrapedMedia.status,
                    ScrapedMedia.media_type,
                    ScrapedMedia.error_message,
                    ScrapedMedia.tenant_id,
                    ScrapedMedia.school_id,
                    ScrapedMedia.school_org_code,
                    ScrapedMedia.school_name,
                    ScrapedMedia.document_id,
                    ScrapedMedia.s3_key_text,
                    ScrapedMedia.source_media_url,
                    ScrapedMedia.id,
                ).where(
                    ScrapedMedia.status.in_(EMPTY_EXTRACTION_STATUSES),
                    *tenant_filter,
                )
            )
        ).all()

        by_status = Counter(r.status for r in empty_rows)
        by_media_type = Counter(r.media_type or "(none)" for r in empty_rows)
        by_error = Counter(_bucket_error(r.error_message) for r in empty_rows)
        by_tenant = Counter(r.tenant_id for r in empty_rows)
        with_document = sum(1 for r in empty_rows if r.document_id is not None)
        with_s3_text = sum(1 for r in empty_rows if r.s3_key_text)

        # Per-school top offenders (for scoping a re-ingest plan)
        by_school = Counter(
            (r.tenant_id, r.school_id, r.school_org_code or "?", r.school_name or "?")
            for r in empty_rows
        )

        # --- Documents with no extracted chunks (different failure mode) ---
        doc_stmt = select(
            Document.processing_status,
            func.count(),
        ).where(
            (Document.chunk_count.is_(None)) | (Document.chunk_count == 0)
        )
        if tenant_id is not None:
            doc_stmt = doc_stmt.where(Document.tenant_id == tenant_id)
        doc_stmt = doc_stmt.group_by(Document.processing_status)
        zero_chunk_docs = dict(
            (status.value if hasattr(status, "value") else status, count)
            for status, count in (await db.execute(doc_stmt)).all()
        )
        zero_chunk_total = sum(zero_chunk_docs.values())

        scope = f"tenant_id={tenant_id}" if tenant_id is not None else "ALL tenants"
        print(f"=== Empty extraction audit ({scope}) ===\n")
        print(f"scraped_media total: {media_total:,}\n")

        print("-- scraped_media by status (all) --")
        for status, count in status_rows:
            marker = "  <-- empty extraction" if status in EMPTY_EXTRACTION_STATUSES else ""
            pct = count / media_total if media_total else 0
            print(f"  {status:24s}: {count:7,}  ({pct:5.1%}){marker}")

        print(
            f"\n-- Empty extraction statuses "
            f"(no usable text → no Document created) n={len(empty_rows):,} --"
        )
        for status, count in by_status.most_common():
            print(f"  {status:24s}: {count:7,}")
        print(f"  {'TOTAL':24s}: {len(empty_rows):7,}")
        print(f"  of which still have document_id set: {with_document:,}  (expect 0)")
        print(f"  of which have s3_key_text set:       {with_s3_text:,}  (expect 0)")

        print("\n-- By media_type --")
        for mt, count in by_media_type.most_common():
            print(f"  {mt:16s}: {count:7,}")

        print("\n-- By error_message bucket --")
        for bucket, count in by_error.most_common():
            print(f"  {bucket:40s}: {count:7,}")

        if tenant_id is None:
            print("\n-- By tenant_id --")
            for tid, count in by_tenant.most_common():
                print(f"  tenant {tid}: {count:7,}")

        print("\n-- Top schools by empty-extraction count --")
        for (tid, sid, org, name), count in by_school.most_common(15):
            print(
                f"  tenant={tid} school_id={sid} org={org:8s} "
                f"n={count:5,}  {name[:60]}"
            )

        print(
            f"\n-- Documents with chunk_count == 0 "
            f"(created, but no extracted/embedded data) n={zero_chunk_total:,} --"
        )
        if not zero_chunk_docs:
            print("  (none)")
        else:
            for status, count in sorted(
                zero_chunk_docs.items(), key=lambda x: -x[1]
            ):
                print(f"  {status:16s}: {count:7,}")
            # Highlight completed-with-zero — the silent Qdrant failure case
            completed_zero = zero_chunk_docs.get(ProcessingStatus.COMPLETED.value, 0)
            if completed_zero:
                print(
                    f"  NOTE: {completed_zero:,} COMPLETED with 0 chunks "
                    "(possible silent Qdrant write failure — see "
                    "tenant_qdrant_chunk_audit.py)"
                )

        if samples > 0 and empty_rows:
            print(f"\n-- Sample empty-extraction rows (up to {samples}) --")
            for r in empty_rows[:samples]:
                err = (r.error_message or "")[:80]
                print(
                    f"  id={r.id} status={r.status} type={r.media_type} "
                    f"school={r.school_org_code} "
                    f"url={r.source_media_url[:90]}"
                )
                if err:
                    print(f"       error: {err}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--tenant-id",
        type=int,
        default=None,
        help="Scope to one tenant (default: all tenants)",
    )
    parser.add_argument(
        "--samples",
        type=int,
        default=0,
        help="Print N sample empty-extraction rows (default: 0)",
    )
    args = parser.parse_args()
    try:
        asyncio.run(audit(args.tenant_id, args.samples))
    except KeyboardInterrupt:
        sys.exit(130)
