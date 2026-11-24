"""Ingestion status report — formatted for email/status updates.

Prints a high-level overview of the school-scraper ingestion pipeline for
one tenant, formatted as a copy-pasteable status block:

  - Overview Metrics (discovered / scraped / ingested / failed / backlog)
  - Data Types Scraped (by file extension)
  - Year Coverage (by doc_year)
  - Common Failures Breakdown (top error categories)

Usage:
    docker exec just-edtech-api python scripts/school_data/ingestion_status_report.py
    docker exec just-edtech-api python scripts/school_data/ingestion_status_report.py --tenant-id 4
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from sqlalchemy import func, select

from app.db.connector import AsyncSessionLocal
from app.models.documents import Document
from app.models.school import ScrapedMedia

BARRIER = "_" * 85


def _fmt(n: int) -> str:
    return f"{n:,}"


async def collect(tenant_id: int) -> dict:
    async with AsyncSessionLocal() as db:
        # --- ScrapedMedia overview ---
        media_total = (
            await db.execute(
                select(func.count())
                .select_from(ScrapedMedia)
                .where(ScrapedMedia.tenant_id == tenant_id)
            )
        ).scalar_one()

        media_by_status = dict(
            (
                await db.execute(
                    select(ScrapedMedia.status, func.count())
                    .where(ScrapedMedia.tenant_id == tenant_id)
                    .group_by(ScrapedMedia.status)
                )
            ).all()
        )

        # Bucket the per-status counts into the overview categories.
        ingested_statuses = {"ingested"}
        scraper_terminal_fail = {
            "failed",
            "skipped_too_large",
            "skipped_no_audio",
            "skipped_silence",
        }
        skipped_other = {
            "skipped_year",
            "skipped_duplicate",
            "skipped_unsupported",
            "skipped",
        }

        discovered = media_total
        scraped_enqueued = media_total - sum(
            c for s, c in media_by_status.items() if s in {"discovered"}
        )
        fully_ingested = sum(
            c for s, c in media_by_status.items() if s in ingested_statuses
        )
        scraper_terminal = sum(
            c for s, c in media_by_status.items() if s in scraper_terminal_fail
        )
        scrape_ok_ingest_fail = sum(
            c
            for s, c in media_by_status.items()
            if s in {"ingest_failed", "ingestion_failed"}
        )
        backlog = sum(
            c
            for s, c in media_by_status.items()
            if s in {"discovered", "pending", "processing", "queued"}
        )

        # --- Data types (file_extension) ---
        ext_rows = (
            await db.execute(
                select(ScrapedMedia.file_extension, func.count())
                .where(
                    ScrapedMedia.tenant_id == tenant_id,
                    ScrapedMedia.file_extension.isnot(None),
                )
                .group_by(ScrapedMedia.file_extension)
                .order_by(func.count().desc())
            )
        ).all()
        data_types = [(ext or "unknown", cnt) for ext, cnt in ext_rows]

        # --- Year coverage (doc_year) ---
        year_rows = (
            await db.execute(
                select(ScrapedMedia.doc_year, func.count())
                .where(
                    ScrapedMedia.tenant_id == tenant_id,
                    ScrapedMedia.doc_year.isnot(None),
                )
                .group_by(ScrapedMedia.doc_year)
                .order_by(ScrapedMedia.doc_year.desc())
            )
        ).all()
        year_coverage = [(int(y), c) for y, c in year_rows]

        # --- Document processing status ---
        doc_rows = (
            await db.execute(
                select(Document.processing_status, func.count())
                .join(
                    ScrapedMedia, ScrapedMedia.document_id == Document.id
                )
                .where(ScrapedMedia.tenant_id == tenant_id)
                .group_by(Document.processing_status)
            )
        ).all()
        docs_by_status = {status.value: count for status, count in doc_rows}
        docs_total = sum(docs_by_status.values())

        # --- Common failures: scraped_media error categories ---
        fail_conditions = [
            ("Empty / Missing Transcript (No speech detected in media)",
             ["no speech detected", "empty transcript", "no audio",
              "silence", "transcript is empty"]),
            ("404 Not Found (Broken/removed source links)",
             ["404", "not found", "broken link"]),
            ("SSL Certificate Verification Failed (Districts with missing cert chains)",
             ["ssl", "certificate", "cert chain"]),
            ("403 Forbidden (Bot blocked / access-denied)",
             ["403", "forbidden", "access denied", "bot blocked"]),
            ("Failed to Open Stream (Transient network/stream errors)",
             ["stream", "connection", "timeout", "network",
              "connectionreset", "remoteend"]),
            ("Corrupt / Invalid Office Archives",
             ["corrupt", "invalid", "bad zip", "is not a zip file",
              "unsupported format"]),
            ("Missing System Dependency (DOC extraction requires antiword)",
             ["antiword", "missing dependency", "command not found"]),
            ("410 Gone (Permanently removed Google Docs links)",
             ["410", "gone", "permanently removed"]),
            ("YouTube Caption / Download Failed",
             ["youtube", "caption", "yt-dlp", "ip blocked",
              "sign in to confirm"]),
            ("AssemblyAI Transcription Failed",
             ["assemblyai", "transcription failed", "rate limit"]),
        ]

        failure_counts: list[tuple[str, int]] = []
        for label, patterns in fail_conditions:
            count = 0
            for pat in patterns:
                result = await db.execute(
                    select(func.count())
                    .select_from(ScrapedMedia)
                    .where(
                        ScrapedMedia.tenant_id == tenant_id,
                        ScrapedMedia.status.in_(
                            list({"failed", "ingest_failed", "ingestion_failed"})
                        ),
                        ScrapedMedia.error_message.ilike(f"%{pat}%"),
                    )
                )
                count += result.scalar_one()
            if count:
                failure_counts.append((label, count))
        failure_counts.sort(key=lambda x: -x[1])

        return {
            "tenant_id": tenant_id,
            "media_total": media_total,
            "discovered": discovered,
            "scraped_enqueued": scraped_enqueued,
            "fully_ingested": fully_ingested,
            "scrape_ok_ingest_fail": scrape_ok_ingest_fail,
            "scraper_terminal": scraper_terminal,
            "backlog": backlog,
            "data_types": data_types,
            "year_coverage": year_coverage,
            "docs_by_status": docs_by_status,
            "docs_total": docs_total,
            "failure_counts": failure_counts,
        }


async def run_report(tenant_id: int) -> None:
    d = await collect(tenant_id)

    print()
    print("Overview Metrics")
    print(BARRIER)
    print(f"  - Discovered: {_fmt(d['discovered'])}")
    print(f"  - Scraped & Enqueued: {_fmt(d['scraped_enqueued'])}")
    print(f"  - Fully Ingested (End-to-End): {_fmt(d['fully_ingested'])}")
    print(f"  - Scraped OK, Ingestion Failed: {_fmt(d['scrape_ok_ingest_fail'])}")
    print(f"  - Scraper-Terminal Failed: {_fmt(d['scraper_terminal'])}")
    print(f"  - Backlog / In-Flight: {_fmt(d['backlog'])}")
    print(BARRIER)
    print()
    print("Data Types Scraped")
    print(BARRIER)
    for ext, cnt in d["data_types"]:
        label = ext if ext.startswith(".") else f".{ext}"
        print(f"  - {label}: {_fmt(cnt)}")
    print(BARRIER)
    print()
    print("Year Coverage")
    print(BARRIER)
    for year, cnt in d["year_coverage"]:
        print(f"  - {year}: {_fmt(cnt)}")
    print(BARRIER)
    print()
    print("Document Processing Status")
    print(BARRIER)
    for status in ("completed", "processing", "pending", "failed", "skipped"):
        cnt = d["docs_by_status"].get(status, 0)
        if cnt:
            print(f"  - {status}: {_fmt(cnt)}")
    print(f"  - total: {_fmt(d['docs_total'])}")
    print(BARRIER)
    print()
    print("Common Failures Breakdown")
    print(BARRIER)
    if d["failure_counts"]:
        for label, cnt in d["failure_counts"]:
            print(f"  - {label}: {_fmt(cnt)}")
    else:
        print("  (no categorized failures)")
    print(BARRIER)


async def main() -> int:
    parser = argparse.ArgumentParser(
        description="Print ingestion status report for a tenant.",
    )
    parser.add_argument(
        "--tenant-id",
        type=int,
        default=4,
        help="Tenant ID to report on (default: 4).",
    )
    args = parser.parse_args()
    await run_report(args.tenant_id)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
