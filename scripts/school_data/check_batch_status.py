#!/usr/bin/env python3
"""
Check DB-side processing status for every district in a finalised batch JSON.

For each school (matched by ``tenant_id`` + ``org_code``) reports:
  - whether the School row exists, and how many of its finalised URLs are
    seeded as active ``school_scrape_urls``
  - ``scraped_media`` counts grouped by status (discovered/ingested/failed/
    skipped_year/skipped_duplicate/...)
  - ``documents`` counts grouped by ``processing_status``
    (pending/processing/completed/failed/skipped)

Use this to see how far a batch (e.g. "batch 3.json") has progressed through
scraping + ingestion without needing prod shell/log access.

Usage:
    python scripts/school_data/check_batch_status.py
    python scripts/school_data/check_batch_status.py \\
        --json "scripts/school_data/output/Historical Data/batch 3.json" \\
        --tenant-id 4
    python scripts/school_data/check_batch_status.py --tenant-id 4 --out report.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from sqlalchemy import func, select

from app.core.config import settings
from app.db.connector import AsyncSessionLocal
from app.models.documents import Document
from app.models.school import School, ScrapedMedia, SchoolScrapeUrl

DEFAULT_JSON = (
    Path(__file__).parent / "output" / "Historical Data" / "batch 3.json"
)


async def _status_for_school(db, tenant_id: int, org_code: str) -> dict:
    school = (
        await db.execute(
            select(School).where(
                School.tenant_id == tenant_id, School.org_code == org_code
            )
        )
    ).scalar_one_or_none()

    if school is None:
        return {"org_code": org_code, "found": False}

    scrape_url_rows = (
        (
            await db.execute(
                select(SchoolScrapeUrl.is_active, func.count())
                .where(SchoolScrapeUrl.school_id == school.id)
                .group_by(SchoolScrapeUrl.is_active)
            )
        )
        .all()
    )
    active_urls = sum(c for is_active, c in scrape_url_rows if is_active)
    inactive_urls = sum(c for is_active, c in scrape_url_rows if not is_active)

    media_rows = (
        (
            await db.execute(
                select(ScrapedMedia.status, func.count())
                .where(ScrapedMedia.school_id == school.id)
                .group_by(ScrapedMedia.status)
            )
        )
        .all()
    )
    media_by_status = {status: count for status, count in media_rows}

    doc_rows = (
        (
            await db.execute(
                select(Document.processing_status, func.count())
                .join(
                    ScrapedMedia,
                    ScrapedMedia.document_id == Document.id,
                )
                .where(ScrapedMedia.school_id == school.id)
                .group_by(Document.processing_status)
            )
        )
        .all()
    )
    docs_by_status = {status.value: count for status, count in doc_rows}

    return {
        "org_code": org_code,
        "found": True,
        "school_id": school.id,
        "name": school.name,
        "last_scrapped_at": (
            school.last_scrapped_at.isoformat() if school.last_scrapped_at else None
        ),
        "scrape_urls": {"active": active_urls, "inactive": inactive_urls},
        "scraped_media_by_status": media_by_status,
        "documents_by_processing_status": docs_by_status,
    }


async def check_batch_status(json_path: Path, tenant_id: int) -> dict:
    records = json.loads(json_path.read_text(encoding="utf-8"))

    print("=" * 60)
    print("Batch status check")
    print(f"  input      : {json_path}")
    print(f"  tenant_id  : {tenant_id}")
    print(f"  districts  : {len(records)}")
    print("=" * 60)

    results: list[dict] = []
    async with AsyncSessionLocal() as db:
        for rec in records:
            name = rec.get("School name") or rec.get("name") or ""
            org_code = (rec.get("org_code") or "").strip()
            if not org_code:
                results.append({"name": name, "org_code": None, "found": False})
                print(f"  [skip] {name} — no org_code in batch JSON")
                continue

            status = await _status_for_school(db, tenant_id, org_code)
            status["batch_name"] = name
            results.append(status)

            if not status["found"]:
                print(f"  [MISSING] {name} ({org_code}) — no School row for this tenant")
                continue

            media_total = sum(status["scraped_media_by_status"].values())
            docs_total = sum(status["documents_by_processing_status"].values())
            print(
                f"  [ok] {name} ({org_code}) — "
                f"urls active={status['scrape_urls']['active']} "
                f"media={media_total} {status['scraped_media_by_status']} "
                f"docs={docs_total} {status['documents_by_processing_status']}"
            )

    found = [r for r in results if r.get("found")]
    missing = [r for r in results if not r.get("found")]

    media_totals: dict[str, int] = {}
    doc_totals: dict[str, int] = {}
    for r in found:
        for k, v in r["scraped_media_by_status"].items():
            media_totals[k] = media_totals.get(k, 0) + v
        for k, v in r["documents_by_processing_status"].items():
            doc_totals[k] = doc_totals.get(k, 0) + v

    summary = {
        "total_districts": len(records),
        "found_in_db": len(found),
        "missing_from_db": len(missing),
        "missing_org_codes": [r["org_code"] for r in missing],
        "scraped_media_totals": media_totals,
        "documents_totals": doc_totals,
    }

    print("\n" + "=" * 60)
    print("Summary")
    print("=" * 60)
    for k, v in summary.items():
        print(f"  {k:<22}: {v}")

    return {"summary": summary, "districts": results}


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Check DB processing status for a finalised batch JSON."
    )
    parser.add_argument("--json", type=Path, default=DEFAULT_JSON)
    parser.add_argument(
        "--tenant-id", type=int, default=settings.DEFAULT_TENANT_ID
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Optional path to write the full report as JSON.",
    )
    args = parser.parse_args()

    if not args.json.exists():
        print(f"Input JSON not found: {args.json}", file=sys.stderr)
        sys.exit(1)

    try:
        report = asyncio.run(check_batch_status(args.json, args.tenant_id))
    except Exception as exc:
        print(f"\nStatus check failed: {exc}", file=sys.stderr)
        sys.exit(1)

    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(
            json.dumps(report, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        print(f"\nWrote full report: {args.out}")


if __name__ == "__main__":
    main()
