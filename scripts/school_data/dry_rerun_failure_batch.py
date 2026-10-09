#!/usr/bin/env python3
"""Dry-run re-crawl of the 84 failure-batch schools with the new unlocks.

Reads the 84 schools from the failure rerun, runs the real SchemaDrivenCrawler
against each (LLM + network), and writes one JSON file in the same format as
the prior `schema_crawl_results.json` so the two can be diffed directly.

NO database writes, NO persistence to scraped_media / school_scrape_urls /
documents. This is a discovery-only dry run.

Output format (one list of records, identical keys to the prior file):
  name, org_code, website, pages_crawled, llm_calls,
  data_pages, visited_pages, errors, error_details

Usage:
    poetry run python scripts/school_data/dry_rerun_failure_batch.py
    poetry run python scripts/school_data/dry_rerun_failure_batch.py --limit 10
    poetry run python scripts/school_data/dry_rerun_failure_batch.py --concurrency 3
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
import time
from datetime import date
from pathlib import Path
from typing import Any

from app.services.web_scraper.schema_driven_crawler import SchemaDrivenCrawler

FAILURE_SCHOOLS = Path(__file__).resolve().parents[2] / (
    "scripts/school_data/output/schema_crawl_failure_rerun_2026-09-08/schools.json"
)
PRIOR_RESULTS = Path(__file__).resolve().parents[2] / (
    "scripts/school_data/output/schema_crawl_failure_rerun_2026-09-08/schema_crawl_results.json"
)
DEFAULT_OUT = Path(__file__).resolve().parents[2] / (
    "scripts/school_data/output/schema_crawl_failure_rerun_2026-09-14/schema_crawl_results.json"
)

logger = logging.getLogger("dry_rerun")


async def crawl_one(
    record: dict[str, Any],
    crawler: SchemaDrivenCrawler,
) -> dict[str, Any]:
    """Crawl one school and return a record matching the prior JSON schema."""
    name = (record.get("name") or "").strip()
    org_code = (record.get("org_code") or "").strip()
    website = (record.get("website") or "").strip()

    result_record: dict[str, Any] = {
        "name": name,
        "org_code": org_code,
        "website": website,
        "pages_crawled": 0,
        "llm_calls": 0,
        "data_pages": [],
        "visited_pages": [],
        "errors": [],
        "error_details": [],
        "max_pages_limit_reached": False,
    }

    if not website:
        result_record["errors"] = ["missing_website"]
        result_record["error_details"] = [{
            "code": "missing_website",
            "url": "",
            "http_status": None,
            "exception_type": None,
            "exception_message": None,
            "stage": None,
            "html_length": None,
        }]
        return result_record

    try:
        crawl = await crawler.crawl(website)
    except Exception as exc:  # noqa: BLE001
        result_record["errors"] = [f"crawl_raised: {type(exc).__name__}: {exc}"]
        result_record["error_details"] = [{
            "code": "crawl_raised",
            "url": website,
            "http_status": None,
            "exception_type": type(exc).__name__,
            "exception_message": str(exc),
            "stage": None,
            "html_length": None,
        }]
        return result_record

    result_record["pages_crawled"] = crawl.pages_crawled
    result_record["llm_calls"] = crawl.llm_calls
    result_record["data_pages"] = [p.model_dump() for p in crawl.data_pages]
    result_record["visited_pages"] = [p.model_dump() for p in crawl.visited_pages]
    result_record["errors"] = list(crawl.errors)
    result_record["error_details"] = [e.to_dict() for e in crawl.error_details]
    result_record["max_pages_limit_reached"] = crawl.max_pages_limit_reached
    return result_record


async def worker(
    name: str,
    queue: asyncio.Queue,
    results: list[dict[str, Any]],
    results_lock: asyncio.Lock,
    counter: dict[str, int],
    counter_lock: asyncio.Lock,
) -> None:
    """Pull schools from the queue, crawl each with a fresh crawler, append result."""
    while True:
        try:
            idx, record = queue.get_nowait()
        except asyncio.QueueEmpty:
            return
        website = record.get("website", "")
        name_s = record.get("name", "")
        t0 = time.time()
        # Fresh crawler per school so the Playwright browser is closed between
        # schools (avoids state bleed + memory growth across 84 crawls).
        crawler = SchemaDrivenCrawler(
            max_pages=20, skip_archival=False, confidence_threshold=0.5
        )
        try:
            res = await crawl_one(record, crawler)
        finally:
            await crawler.close()
        dt = time.time() - t0
        async with results_lock:
            results.append(res)
        async with counter_lock:
            counter["done"] += 1
            done = counter["done"]
            dp = len(res.get("data_pages") or [])
            pc = res.get("pages_crawled", 0)
            err = len(res.get("errors") or [])
        logger.info(
            "[%d/%d] %s | %s | pages=%d data=%d errors=%d %.1fs",
            done, counter["total"], name_s, website, pc, dp, err, dt,
        )
        queue.task_done()


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", default=str(FAILURE_SCHOOLS),
                        help="Input schools JSON (default: failure rerun schools.json)")
    parser.add_argument("--out", default=str(DEFAULT_OUT),
                        help="Output JSON path")
    parser.add_argument("--limit", type=int, default=0,
                        help="Crawl only the first N schools (0 = all)")
    parser.add_argument("--concurrency", type=int, default=3,
                        help="Number of schools crawled in parallel")
    parser.add_argument("--max-pages", type=int, default=20,
                        help="Max pages per crawl (default 20, matches new config)")
    parser.add_argument("--log-level", default="INFO",
                        choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    args = parser.parse_args()

    logging.basicConfig(
        level=args.log_level,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )

    in_path = Path(args.input)
    out_path = Path(args.out)
    if not in_path.exists():
        print(f"ERROR: input not found: {in_path}", file=sys.stderr)
        return 1
    out_path.parent.mkdir(parents=True, exist_ok=True)

    schools = json.load(open(in_path))
    if args.limit and args.limit > 0:
        schools = schools[:args.limit]
    print(f"Dry-run re-crawl of {len(schools)} failure-batch schools")
    print(f"  input : {in_path}")
    print(f"  out   : {out_path}")
    print(f"  concurrency={args.concurrency}  max_pages={args.max_pages}")
    print(f"  today : {date.today()}")
    print()

    queue: asyncio.Queue = asyncio.Queue()
    for i, r in enumerate(schools):
        queue.put_nowait((i, r))
    results: list[dict[str, Any]] = []
    results_lock = asyncio.Lock()
    counter = {"done": 0, "total": len(schools)}
    counter_lock = asyncio.Lock()

    t0 = time.time()
    workers = [
        asyncio.create_task(
            worker(f"worker-{n}", queue, results, results_lock, counter, counter_lock)
        )
        for n in range(args.concurrency)
    ]
    await queue.join()
    for w in workers:
        w.cancel()
    dt = time.time() - t0

    # Sort results to match the input order (queue may have processed out of order)
    order = {r["name"]: i for i, r in enumerate(schools)}
    results.sort(key=lambda r: order.get(r.get("name", ""), 1_000_000))

    out_path.write_text(json.dumps(results, indent=2, default=str))
    print(f"\nWrote {len(results)} records to {out_path} ({dt:.1f}s)")

    # Quick before/after summary if the prior file exists
    if PRIOR_RESULTS.exists():
        prior = {(p["name"]): p for p in json.load(open(PRIOR_RESULTS))}
        improved, regressed, same = [], [], []
        for r in results:
            p = prior.get(r["name"])
            if not p:
                continue
            before_dp = len(p.get("data_pages") or [])
            after_dp = len(r.get("data_pages") or [])
            if after_dp > before_dp:
                improved.append((r["name"], before_dp, after_dp))
            elif after_dp < before_dp:
                regressed.append((r["name"], before_dp, after_dp))
            else:
                same.append(r["name"])
        print(f"\n=== Before/after summary (vs {PRIOR_RESULTS.name}) ===")
        print(f"  improved (more data_pages): {len(improved)}")
        for n, b, a in improved[:20]:
            print(f"    {n}: {b} -> {a}")
        print(f"  regressed (fewer data_pages): {len(regressed)}")
        for n, b, a in regressed[:10]:
            print(f"    {n}: {b} -> {a}")
        print(f"  unchanged: {len(same)}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
