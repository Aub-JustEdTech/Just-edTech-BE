#!/usr/bin/env python3
"""
Seed tenant-scoped schools + scrape URLs from a finalised batch JSON.

``final batch 1.json`` uses human review field names (``School name``,
``finalised urls``, ``clicks needed``) and does NOT include ``org_code``.
This script:

  1. Resolves ``org_code`` via ``school_names.json`` (exact name, aliases,
     then website hostname fallback).
  2. Merges duplicate districts (same org_code) by unioning URL lists.
  3. Inserts/updates ``schools`` rows for the target tenant (default 4).
  4. Upserts ``school_scrape_urls`` for every finalised URL.
  5. Writes an enriched JSON (feed/scrape compatible) for downstream tools.

Org-code enrichment needed when batch names differ from DOE registry names:

  | Batch name          | Registry name (school_names.json)     | org_code   |
  |---------------------|----------------------------------------|------------|
  | FRSU38              | Frontier                               | 06700000   |
  | Frontier Regional   | Frontier                               | 06700000   |
  | Somerset Berkeley   | Somerset Berkley Regional School District | 07630000 |

All other batch 1 names (105/108) match ``school_names.json`` exactly.

Usage:
    python scripts/school_data/seed_tenant_batch.py --tenant-id 4
    python scripts/school_data/seed_tenant_batch.py --tenant-id 4 --dry-run
    python scripts/school_data/seed_tenant_batch.py \\
        --batch-json "scripts/school_data/output/final batch 1.json" \\
        --output "scripts/school_data/output/final batch 1 enriched.json"
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from sqlalchemy import select

from app.db.connector import AsyncSessionLocal
from app.models.school import School, SchoolScrapeUrl

DEFAULT_BATCH_JSON = (
    Path(__file__).parent / "output" / "final batch 1.json"
)
DEFAULT_SCHOOL_NAMES_JSON = (
    Path(__file__).parent / "output" / "school_names.json"
)
DEFAULT_OUTPUT_JSON = (
    Path(__file__).parent / "output" / "final batch 1 enriched.json"
)
DEFAULT_TENANT_ID = 4

DEFAULT_CRAWL_DEPTH = 2
DEFAULT_USE_PLAYWRIGHT = True

# Batch review names → canonical ``school_names.json`` ``name`` field.
NAME_ALIASES: dict[str, str] = {
    "FRSU38": "Frontier",
    "Frontier Regional": "Frontier",
    "Somerset Berkeley": "Somerset Berkley Regional School District",
}


def _hostname(url: str | None) -> str | None:
    if not url:
        return None
    try:
        host = (urlparse(url.strip()).hostname or "").lower()
        return host[4:] if host.startswith("www.") else host or None
    except ValueError:
        return None


def _load_registry(path: Path) -> tuple[dict[str, dict], dict[str, dict]]:
    """Return (by_name, by_org_code) indexes over school_names.json records."""
    records = json.loads(path.read_text(encoding="utf-8"))
    by_name: dict[str, dict] = {}
    by_org: dict[str, dict] = {}
    for rec in records:
        name = (rec.get("name") or "").strip()
        org = (rec.get("org_code") or "").strip()
        if name:
            by_name[name] = rec
        if org:
            by_org[org] = rec
    return by_name, by_org


def _resolve_registry_name(batch_name: str) -> str:
    batch_name = batch_name.strip()
    if batch_name in NAME_ALIASES:
        return NAME_ALIASES[batch_name]
    return batch_name


def _lookup_registry_record(
    batch_name: str,
    batch_website: str | None,
    by_name: dict[str, dict],
) -> tuple[dict | None, str | None]:
    """Return (registry_record, resolution_note)."""
    canonical = _resolve_registry_name(batch_name)
    if canonical in by_name:
        note = f"alias→{canonical}" if canonical != batch_name.strip() else "exact"
        return by_name[canonical], note

    host = _hostname(batch_website)
    if host:
        for rec in by_name.values():
            reg_host = _hostname(rec.get("website"))
            if reg_host and reg_host == host:
                return rec, f"website→{rec.get('name')}"

    return None, None


def _clicks_to_depth(clicks: int | None) -> int:
    """Map review ``clicks needed`` to scrape ``crawl_depth`` (probe convention)."""
    if clicks is None:
        return DEFAULT_CRAWL_DEPTH
    return max(0, min(4, int(clicks) - 1))


def _url_key(url: str) -> str:
    return url.strip().rstrip("/")


def enrich_batch_records(
    batch_records: list[dict],
    by_name: dict[str, dict],
) -> tuple[list[dict], list[dict]]:
    """
    Build feed-compatible enriched rows and an unresolved report.

    Merges multiple batch rows that resolve to the same org_code.
    """
    merged: dict[str, dict] = {}
    unresolved: list[dict] = []

    for rec in batch_records:
        batch_name = (rec.get("School name") or rec.get("name") or "").strip()
        batch_website = (rec.get("School website") or rec.get("website") or "").strip()
        raw_urls = list(rec.get("finalised urls") or rec.get("urls") or [])
        clicks = rec.get("clicks needed")
        crawl_depth = _clicks_to_depth(clicks)

        registry, how = _lookup_registry_record(batch_name, batch_website, by_name)
        if registry is None:
            unresolved.append(
                {
                    "School name": batch_name,
                    "School website": batch_website,
                    "reason": "no match in school_names.json (exact, alias, or website)",
                }
            )
            continue

        org_code = (registry.get("org_code") or "").strip()
        if not org_code:
            unresolved.append({"School name": batch_name, "reason": "registry row missing org_code"})
            continue

        url_entries: list[dict] = []
        for raw in raw_urls:
            url = str(raw).strip()
            if not url:
                continue
            url_entries.append(
                {
                    "url": url,
                    "crawl_depth": crawl_depth,
                    "use_playwright": DEFAULT_USE_PLAYWRIGHT,
                }
            )

        if org_code not in merged:
            merged[org_code] = {
                "org_code": org_code,
                "name": (registry.get("name") or batch_name).strip(),
                "batch_names": [batch_name],
                "resolution": [how],
                "district_type": registry.get("district_type"),
                "website": registry.get("website") or batch_website or None,
                "urls": [],
            }
        else:
            entry = merged[org_code]
            if batch_name not in entry["batch_names"]:
                entry["batch_names"].append(batch_name)
            if how and how not in entry["resolution"]:
                entry["resolution"].append(how)
            if not entry.get("website") and batch_website:
                entry["website"] = batch_website

        seen = {_url_key(u["url"]) for u in merged[org_code]["urls"]}
        for ue in url_entries:
            key = _url_key(ue["url"])
            if key not in seen:
                seen.add(key)
                merged[org_code]["urls"].append(ue)

    enriched = sorted(merged.values(), key=lambda r: r["org_code"])
    # Strip internal fields for the written JSON (keep batch_names in meta file optional)
    out_rows: list[dict] = []
    for row in enriched:
        out_rows.append(
            {
                "org_code": row["org_code"],
                "name": row["name"],
                "website": row.get("website"),
                "district_type": row.get("district_type"),
                "urls": row["urls"],
                "_batch_names": row["batch_names"],
                "_resolution": row["resolution"],
            }
        )
    return out_rows, unresolved


async def _upsert_scrape_url(
    db,
    *,
    school_id: int,
    url: str,
    crawl_depth: int,
    use_playwright: bool,
) -> bool:
    """Insert or update one scrape URL. Returns True if changed, False if unchanged."""
    existing = (
        await db.execute(
            select(SchoolScrapeUrl).where(
                SchoolScrapeUrl.school_id == school_id,
                SchoolScrapeUrl.url == url,
            )
        )
    ).scalar_one_or_none()

    if existing is not None:
        if (
            existing.is_active
            and existing.crawl_depth == crawl_depth
            and existing.use_playwright == use_playwright
        ):
            return False
        existing.crawl_depth = crawl_depth
        existing.use_playwright = use_playwright
        existing.confirmed_at = datetime.now(timezone.utc)
        existing.is_active = True
        return True

    db.add(
        SchoolScrapeUrl(
            school_id=school_id,
            url=url,
            crawl_depth=crawl_depth,
            use_playwright=use_playwright,
            confirmed_at=datetime.now(timezone.utc),
            is_active=True,
        )
    )
    return True


async def seed_tenant_from_enriched(
    tenant_id: int,
    enriched: list[dict],
    *,
    dry_run: bool,
    feed_urls: bool,
) -> dict:
    stats = {
        "schools_inserted": 0,
        "schools_updated": 0,
        "urls_upserted": 0,
        "urls_unchanged": 0,
        "schools_total": len(enriched),
    }

    async with AsyncSessionLocal() as db:
        for row in enriched:
            org_code = row["org_code"]
            name = row["name"]
            url_configs = row.get("urls") or []

            existing = (
                await db.execute(
                    select(School).where(
                        School.tenant_id == tenant_id, School.org_code == org_code
                    )
                )
            ).scalar_one_or_none()

            if existing:
                if not dry_run:
                    existing.name = name
                    if row.get("district_type"):
                        existing.district_type = row["district_type"]
                    if row.get("website"):
                        existing.website = row["website"]
                    existing.is_active = True
                stats["schools_updated"] += 1
                school = existing
            else:
                if dry_run:
                    school = None
                    stats["schools_inserted"] += 1
                else:
                    school = School(
                        tenant_id=tenant_id,
                        org_code=org_code,
                        name=name,
                        district_type=row.get("district_type") or "Public School District",
                        website=row.get("website"),
                        is_active=True,
                    )
                    db.add(school)
                    await db.flush()
                    stats["schools_inserted"] += 1

            if not feed_urls or not url_configs:
                continue

            if dry_run:
                stats["urls_upserted"] += len(url_configs)
                continue

            if school is None:
                school = (
                    await db.execute(
                        select(School).where(
                            School.tenant_id == tenant_id, School.org_code == org_code
                        )
                    )
                ).scalar_one()

            school_id = school.id

            for cfg in url_configs:
                changed = await _upsert_scrape_url(
                    db,
                    school_id=school_id,
                    url=cfg["url"],
                    crawl_depth=cfg["crawl_depth"],
                    use_playwright=cfg["use_playwright"],
                )
                if changed:
                    stats["urls_upserted"] += 1
                else:
                    stats["urls_unchanged"] += 1

        if not dry_run:
            await db.commit()

    return stats


async def run(
    *,
    tenant_id: int,
    batch_json: Path,
    school_names_json: Path,
    output_json: Path,
    dry_run: bool,
    feed_urls: bool,
) -> dict:
    print("=" * 60)
    print("Tenant batch seeder (org_code enrichment + schools + URLs)")
    print(f"  tenant_id        : {tenant_id}")
    print(f"  batch_json       : {batch_json}")
    print(f"  school_names     : {school_names_json}")
    print(f"  output_json      : {output_json}")
    print(f"  dry_run          : {dry_run}")
    print(f"  feed_urls        : {feed_urls}")
    print("=" * 60)

    if not batch_json.exists():
        raise FileNotFoundError(f"Batch JSON not found: {batch_json}")
    if not school_names_json.exists():
        raise FileNotFoundError(f"School names JSON not found: {school_names_json}")

    batch_records = json.loads(batch_json.read_text(encoding="utf-8"))
    by_name, _ = _load_registry(school_names_json)

    enriched, unresolved = enrich_batch_records(batch_records, by_name)

    print(f"\nBatch rows       : {len(batch_records)}")
    print(f"Enriched schools : {len(enriched)} (after org_code merge)")
    print(f"Unresolved       : {len(unresolved)}")

    if unresolved:
        print("\nUnresolved (fix NAME_ALIASES or school_names.json):")
        for u in unresolved:
            print(f"  - {u.get('School name')!r}: {u.get('reason')}")

    # Write enriched JSON for run_scrape_districts / feed_finalised_scrape_urls
    output_payload = [
        {
            "org_code": r["org_code"],
            "name": r["name"],
            "website": r.get("website"),
            "urls": r["urls"],
        }
        for r in enriched
    ]
    if not dry_run:
        output_json.write_text(
            json.dumps(output_payload, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        print(f"\nWrote enriched JSON: {output_json}")
    else:
        print(f"\n[dry-run] Would write {len(output_payload)} schools to {output_json}")

    if dry_run:
        print("\n[dry-run] Skipping database writes.")
        stats = {
            "schools_total": len(enriched),
            "urls_total": sum(len(r.get("urls") or []) for r in enriched),
        }
    else:
        stats = await seed_tenant_from_enriched(
            tenant_id, enriched, dry_run=False, feed_urls=feed_urls
        )

    print("\nSeed results:")
    for k, v in stats.items():
        print(f"  {k:<18}: {v}")

    if unresolved:
        print("\nStopped with unresolved names — fix aliases before prod ingest.")
        return {"stats": stats, "unresolved": unresolved, "enriched_count": len(enriched)}

    print("\nDone.")
    return {"stats": stats, "unresolved": [], "enriched_count": len(enriched)}


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Seed a tenant from final batch JSON with org_code enrichment."
    )
    parser.add_argument(
        "--tenant-id",
        type=int,
        default=DEFAULT_TENANT_ID,
        help=f"Target tenant (default: {DEFAULT_TENANT_ID}).",
    )
    parser.add_argument(
        "--batch-json",
        type=Path,
        default=DEFAULT_BATCH_JSON,
        help="Finalised batch JSON (School name / finalised urls shape).",
    )
    parser.add_argument(
        "--school-names",
        type=Path,
        default=DEFAULT_SCHOOL_NAMES_JSON,
        help="DOE registry JSON with org_code + canonical names.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT_JSON,
        help="Where to write feed-compatible enriched JSON.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Resolve and print stats without writing DB or output file.",
    )
    parser.add_argument(
        "--no-feed-urls",
        action="store_true",
        help="Only seed school rows; skip school_scrape_urls upsert.",
    )
    args = parser.parse_args()

    try:
        result = asyncio.run(
            run(
                tenant_id=args.tenant_id,
                batch_json=args.batch_json,
                school_names_json=args.school_names,
                output_json=args.output,
                dry_run=args.dry_run,
                feed_urls=not args.no_feed_urls,
            )
        )
        if result.get("unresolved"):
            sys.exit(1)
    except Exception as exc:
        print(f"\nSeed failed: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
