#!/usr/bin/env python3
"""
Crawl confirmed scrape URLs, persist ScrapedMedia rows, and enqueue ingestion.

This is the missing bridge between URL confirmation (feed_finalised_scrape_urls.py)
and the existing ingest_scraped_media Celery task.

For each school with a primary scrape URL:
  1. Call SchoolScraperService.scrape_media_files() on the confirmed page.
  2. Insert new ScrapedMedia rows (deduped by url_hash per school).
  3. Optionally enqueue ingest_scraped_media for each new row.

Idempotent: re-running skips URLs already recorded for a school.

Usage:
    python scripts/school_data/run_scrape_districts.py
    python scripts/school_data/run_scrape_districts.py --dry-run
    python scripts/school_data/run_scrape_districts.py \\
        --json scripts/school_data/output/finalised_20_disticts.json \\
        --crawl-depth 2 --concurrency 2
    python scripts/school_data/run_scrape_districts.py --no-enqueue
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.core.config import settings
from app.crud import schools as crud
from app.db.connector import AsyncSessionLocal
from app.models.school import School, SchoolScrapeUrl, ScrapedMedia
from app.services.web_scraper.school_scraper_service import SchoolScraperService
from app.services.web_scraper.year_filter import evaluate_media_year_async

DEFAULT_JSON_PATH = (
    Path(__file__).parent / "output" / "finalised_20_disticts.json"
)


def _classify_media_type(reported: str | None, ext: str | None) -> str:
    if reported == "document":
        return "document"
    if reported == "youtube":
        return "youtube"
    if reported in ("video", "audio"):
        if ext and "youtube" in ext.lower():
            return "youtube"
        return reported
    e = (ext or "").lower().lstrip(".")
    if e in ("mp4", "mov", "webm"):
        return "video"
    if e in ("mp3", "wav", "m4a"):
        return "audio"
    return "document"


async def _load_target_org_codes(
    json_path: Path | None, org_codes_arg: list[str] | None
) -> set[str] | None:
    if org_codes_arg:
        return {c.strip() for c in org_codes_arg if c.strip()}
    if json_path is None:
        return None
    if not json_path.exists():
        raise FileNotFoundError(f"JSON not found: {json_path}")
    with json_path.open("r", encoding="utf-8") as f:
        records = json.load(f)
    return {str(r.get("org_code", "")).strip() for r in records if r.get("org_code")}


async def _load_schools(
    tenant_id: int, org_codes: set[str] | None
) -> list[tuple[School, SchoolScrapeUrl]]:
    async with AsyncSessionLocal() as db:
        stmt = (
            select(School)
            .where(
                School.tenant_id == tenant_id,
                School.is_active.is_(True),
                School.scrape_url_id.isnot(None),
            )
            .options(selectinload(School.primary_scrape_url))
        )
        if org_codes:
            stmt = stmt.where(School.org_code.in_(sorted(org_codes)))
        schools = list((await db.execute(stmt)).scalars().all())

        pairs: list[tuple[School, SchoolScrapeUrl]] = []
        for school in schools:
            scrape_url = school.primary_scrape_url
            if scrape_url and scrape_url.is_active:
                pairs.append((school, scrape_url))
        return pairs


async def _scrape_one_school(
    school: School,
    scrape_url: SchoolScrapeUrl,
    *,
    crawl_depth: int | None,
    use_playwright: bool | None,
    dry_run: bool,
    enqueue: bool,
) -> dict:
    depth = crawl_depth if crawl_depth is not None else scrape_url.crawl_depth
    pw = use_playwright if use_playwright is not None else scrape_url.use_playwright

    result: dict = {
        "org_code": school.org_code,
        "name": school.name,
        "url": scrape_url.url,
        "crawl_depth": depth,
        "use_playwright": pw,
        "pages_crawled": 0,
        "media_found": 0,
        "media_new": 0,
        "media_skipped": 0,
        "media_skipped_year": 0,
        "enqueue_count": 0,
        "error": None,
    }

    try:
        async with SchoolScraperService(use_playwright=pw) as svc:
            scrape_result = await svc.scrape_media_files(
                page_url=scrape_url.url,
                crawl_depth=depth,
            )
    except Exception as exc:  # noqa: BLE001
        result["error"] = str(exc)
        print(f"  [fail] {school.name} ({school.org_code}) — {exc}")
        return result

    media_files = scrape_result.get("media_files", [])
    result["pages_crawled"] = scrape_result.get("pages_crawled", 0)
    result["media_found"] = len(media_files)

    if dry_run:
        docs = sum(1 for m in media_files if m.get("media_type") == "document")
        print(
            f"  [dry]  {school.name} ({school.org_code}) — "
            f"{len(media_files)} media ({docs} docs) across "
            f"{result['pages_crawled']} pages"
        )
        return result

    from app.tasks.school_scraper_tasks import ingest_scraped_media

    async with AsyncSessionLocal() as db:
        school_row = await db.get(School, school.id)
        if not school_row:
            result["error"] = "school missing on persist"
            return result

        for mf in media_files:
            inferred_year, should_process, _skip_reason = await evaluate_media_year_async(
                url=mf["url"],
                filename=mf.get("name"),
                source_page_url=mf.get("source_page_url", scrape_url.url),
            )
            if not should_process:
                result["media_skipped_year"] += 1
                continue

            uh = crud.url_hash(mf["url"])
            existing = await crud.get_scraped_media_by_url_hash(db, school_row.id, uh)
            if existing:
                result["media_skipped"] += 1
                continue

            media_type = _classify_media_type(
                mf.get("media_type"), mf.get("file_extension")
            )
            sm = ScrapedMedia(
                tenant_id=school_row.tenant_id,
                school_id=school_row.id,
                school_org_code=school_row.org_code,
                school_name=school_row.name,
                district_type=school_row.district_type,
                source_page_url=mf.get("source_page_url", scrape_url.url),
                source_media_url=mf["url"],
                url_hash=uh,
                content_hash=None,
                media_type=media_type,
                file_extension=mf.get("file_extension"),
                original_name=mf.get("name"),
                doc_year=inferred_year,
                status="discovered",
            )
            db.add(sm)
            await db.commit()
            result["media_new"] += 1

            if enqueue:
                # Commit above (not just flush) matters: the Celery worker
                # opens its own DB connection and won't see a merely-flushed
                # row from this session, so an enqueue before commit races
                # the worker into "ScrapedMedia not found" — a silent,
                # non-retried no-op, not a task failure.
                ingest_scraped_media.delay(scraped_media_id=sm.id)
                result["enqueue_count"] += 1

        scrape_url_row = await db.get(SchoolScrapeUrl, scrape_url.id)
        if scrape_url_row:
            scrape_url_row.last_crawl_page_count = result["pages_crawled"]

        await crud.touch_last_scrapped(db, school_row.id)
        await db.commit()

    print(
        f"  [ok]   {school.name} ({school.org_code}) — "
        f"found={result['media_found']} new={result['media_new']} "
        f"skipped={result['media_skipped']} "
        f"skipped_year={result['media_skipped_year']} "
        f"enqueued={result['enqueue_count']}"
    )
    return result


async def run_scrape_districts(
    *,
    tenant_id: int,
    json_path: Path | None,
    org_codes_arg: list[str] | None,
    crawl_depth: int | None,
    use_playwright: bool | None,
    concurrency: int,
    dry_run: bool,
    enqueue: bool,
) -> dict:
    org_codes = await _load_target_org_codes(json_path, org_codes_arg)
    pairs = await _load_schools(tenant_id, org_codes)

    print("=" * 60)
    print("Just-EdTech District Scrape Runner")
    print(f"  tenant_id   : {tenant_id}")
    print(f"  schools     : {len(pairs)}")
    print(f"  crawl_depth : {crawl_depth if crawl_depth is not None else '(per URL)'}")
    print(f"  concurrency : {concurrency}")
    print(f"  dry_run     : {dry_run}")
    print(f"  enqueue     : {enqueue and not dry_run}")
    print("=" * 60)

    if not pairs:
        print("No schools with confirmed scrape URLs found.")
        return {"schools": 0, "results": []}

    sem = asyncio.Semaphore(max(1, concurrency))
    results: list[dict] = []

    async def _run(pair: tuple[School, SchoolScrapeUrl]) -> dict:
        school, scrape_url = pair
        async with sem:
            return await _scrape_one_school(
                school,
                scrape_url,
                crawl_depth=crawl_depth,
                use_playwright=use_playwright,
                dry_run=dry_run,
                enqueue=enqueue,
            )

    tasks = [_run(pair) for pair in pairs]
    results = await asyncio.gather(*tasks)

    stats = {
        "schools": len(results),
        "ok": sum(1 for r in results if not r.get("error")),
        "failed": sum(1 for r in results if r.get("error")),
        "media_found": sum(r.get("media_found", 0) for r in results),
        "media_new": sum(r.get("media_new", 0) for r in results),
        "media_skipped": sum(r.get("media_skipped", 0) for r in results),
        "media_skipped_year": sum(r.get("media_skipped_year", 0) for r in results),
        "enqueue_count": sum(r.get("enqueue_count", 0) for r in results),
    }

    print("\nScrape results:")
    for k, v in stats.items():
        print(f"  {k:<16}: {v}")
    print("\nDone.")
    return {"stats": stats, "results": results}


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Scrape confirmed district URLs and enqueue document ingestion."
    )
    parser.add_argument(
        "--json",
        type=Path,
        default=DEFAULT_JSON_PATH,
        help=(
            "Limit to org_codes listed in this JSON "
            "(default: finalised_20_disticts.json). Pass --all to scrape every "
            "school with a confirmed URL."
        ),
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Scrape all schools with a primary scrape URL (ignore --json filter).",
    )
    parser.add_argument(
        "--org-codes",
        nargs="+",
        help="Limit to these org_codes (overrides --json filter).",
    )
    parser.add_argument(
        "--tenant-id",
        type=int,
        default=settings.DEFAULT_TENANT_ID,
        help=f"Tenant ID (default: {settings.DEFAULT_TENANT_ID}).",
    )
    parser.add_argument(
        "--crawl-depth",
        type=int,
        default=2,
        help="Override crawl depth for archive sub-pages (default: 2).",
    )
    parser.add_argument(
        "--use-playwright",
        action="store_true",
        help="Force Playwright for all schools (auto-detect is default).",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=2,
        help="Max concurrent school scrapes (default: 2).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Crawl and report counts without writing to DB or enqueueing.",
    )
    parser.add_argument(
        "--no-enqueue",
        action="store_true",
        help="Persist ScrapedMedia rows but do not enqueue ingest tasks.",
    )
    args = parser.parse_args()

    json_path = None if args.all else args.json
    use_playwright = True if args.use_playwright else None

    try:
        asyncio.run(
            run_scrape_districts(
                tenant_id=args.tenant_id,
                json_path=json_path,
                org_codes_arg=args.org_codes,
                crawl_depth=args.crawl_depth,
                use_playwright=use_playwright,
                concurrency=args.concurrency,
                dry_run=args.dry_run,
                enqueue=not args.no_enqueue,
            )
        )
    except Exception as exc:
        print(f"\nScrape run failed: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
