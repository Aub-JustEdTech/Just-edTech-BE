#!/usr/bin/env python3
"""
POC runner for the schema-driven school-site crawler.

Crawls every school in scripts/school_data/output/selected_schools.json
using SchemaDrivenCrawler (LLM page classification + ranked frontier)
and writes the discovered data pages to a separate output file so the
results can be diffed against the keyword-based baseline in
school_url_candidates.json.

Usage:
    python -m scripts.school_data.schema_crawl_poc.run_poc
    python -m scripts.school_data.schema_crawl_poc.run_poc \\
        --json scripts/school_data/output/selected_schools.json \\
        --out scripts/school_data/output/schema_crawl_results.json \\
        --max-pages 8 --concurrency 3 --include-archival

This script is on the experiment branch only. It does NOT modify any
file under app/ and does NOT call SchoolScraperService.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path
from typing import Any

from scripts.school_data.schema_crawl_poc.classifier import PageClassifier
from scripts.school_data.schema_crawl_poc.crawler import SchemaDrivenCrawler

DEFAULT_JSON_PATH = (
    Path(__file__).resolve().parents[1] / "output" / "selected_schools.json"
)
DEFAULT_OUT_PATH = (
    Path(__file__).resolve().parents[1] / "output" / "schema_crawl_results.json"
)

logger = logging.getLogger("schema_crawl_poc")


async def crawl_one(
    record: dict[str, Any],
    crawler: SchemaDrivenCrawler,
) -> dict[str, Any]:
    name = (record.get("name") or "").strip()
    org_code = (record.get("org_code") or "").strip()
    website = (record.get("website") or "").strip()

    result: dict[str, Any] = {
        "name": name,
        "org_code": org_code,
        "website": website,
        "pages_crawled": 0,
        "llm_calls": 0,
        "data_pages": [],
        "visited_pages": [],
        "errors": [],
    }

    if not website:
        result["errors"] = ["missing_website"]
        return result

    try:
        crawl = await crawler.crawl(website)
    except Exception as exc:  # noqa: BLE001
        result["errors"] = [f"{type(exc).__name__}: {exc}"]
        return result

    result["pages_crawled"] = crawl.pages_crawled
    result["llm_calls"] = crawl.llm_calls
    result["data_pages"] = [p.model_dump() for p in crawl.data_pages]
    result["visited_pages"] = [p.model_dump() for p in crawl.visited_pages]
    result["errors"] = crawl.errors
    return result


async def run(
    json_path: Path,
    out_path: Path,
    max_pages: int,
    confidence_threshold: float,
    concurrency: int,
    include_archival: bool,
    model: str | None,
    limit: int | None,
    offset: int,
) -> None:
    if not json_path.exists():
        print(f"Input JSON not found: {json_path}", file=sys.stderr)
        sys.exit(1)

    with json_path.open("r", encoding="utf-8") as f:
        all_records = json.load(f)

    # Apply --offset / --limit so we can run a slice of the schools
    # (e.g. first 20) without creating a separate JSON file.
    total_available = len(all_records)
    if offset < 0:
        print(f"--offset must be >= 0, got {offset}", file=sys.stderr)
        sys.exit(1)
    if offset >= total_available:
        print(
            f"--offset {offset} is past end of input "
            f"(only {total_available} schools available)",
            file=sys.stderr,
        )
        sys.exit(1)
    records = all_records[offset:]
    if limit is not None and limit > 0:
        records = records[:limit]

    print("=" * 70)
    print("Just-EdTech Schema-Driven Crawler POC")
    print(f"  input              : {json_path}")
    print(f"  output             : {out_path}")
    print(
        f"  schools            : {len(records)} "
        f"(offset={offset}, limit={limit or 'all'}, total_available={total_available})"
    )
    print(f"  max_pages/school   : {max_pages}")
    print(f"  confidence >=      : {confidence_threshold}")
    print(f"  include archival   : {include_archival}")
    print(f"  concurrency        : {concurrency}")
    print(f"  model              : {model or '(default from settings)'}")
    print("=" * 70)

    classifier = PageClassifier(model=model)
    sem = asyncio.Semaphore(concurrency)

    async def _worker(idx: int, record: dict[str, Any]) -> dict[str, Any]:
        async with sem:
            # One crawler per school — they're cheap (just hold config).
            crawler = SchemaDrivenCrawler(
                classifier=classifier,
                max_pages=max_pages,
                confidence_threshold=confidence_threshold,
                skip_archival=not include_archival,
            )
            res = await crawl_one(record, crawler)
            n_data = len(res.get("data_pages", []))
            n_visited = res.get("pages_crawled", 0)
            n_llm = res.get("llm_calls", 0)
            err = res.get("errors")
            status = "OK " if not err else "ERR"
            print(
                f"  [{status}] {res['name']:<30} ({res['org_code']}) "
                f"-> {n_data} data pages, {n_visited} visited, {n_llm} LLM calls"
                + (f"  ({'; '.join(err)})" if err else "")
            )
            return res

    tasks = [
        asyncio.create_task(_worker(i, rec), name=rec.get("name", f"school-{i}"))
        for i, rec in enumerate(records)
    ]

    results: list[dict[str, Any]] = []
    for coro in asyncio.as_completed(tasks):
        results.append(await coro)

    # Preserve input order.
    order = {r.get("org_code"): i for i, r in enumerate(records)}
    results.sort(key=lambda r: order.get(r.get("org_code"), 0))

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)

    total_data = sum(len(r.get("data_pages", [])) for r in results)
    total_llm = sum(r.get("llm_calls", 0) for r in results)
    errors = sum(1 for r in results if r.get("errors"))

    print("\n" + "=" * 70)
    print("Summary")
    print("=" * 70)
    print(f"  schools processed  : {len(results)}")
    print(f"  errors             : {errors}")
    print(f"  total data pages   : {total_data}")
    print(f"  total LLM calls    : {total_llm}")
    print(f"\nOutput written to: {out_path}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Schema-driven crawler POC over selected_schools.json."
    )
    parser.add_argument("--json", type=Path, default=DEFAULT_JSON_PATH)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT_PATH)
    parser.add_argument("--max-pages", type=int, default=10)
    parser.add_argument(
        "--confidence",
        type=float,
        default=0.5,
        help="Minimum LLM confidence for a sub-link to enter the frontier.",
    )
    parser.add_argument("--concurrency", type=int, default=3)
    parser.add_argument(
        "--include-archival",
        action="store_true",
        help="Do NOT skip pages the LLM marks as archival.",
    )
    parser.add_argument(
        "--model",
        type=str,
        default=None,
        help="Override the LLM model (default: settings.HEATMAP_INGEST_DOC_CLASSIFIER_MODEL).",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Process only the first N schools (after --offset). "
        "Useful for a quick 20-school trial without splitting the JSON.",
    )
    parser.add_argument(
        "--offset",
        type=int,
        default=0,
        help="Skip the first N schools before applying --limit (default 0).",
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true", help="Enable debug logging."
    )
    args = parser.parse_args()

    if args.limit is not None and args.limit <= 0:
        print("--limit must be a positive integer (or omit for all).", file=sys.stderr)
        sys.exit(1)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    try:
        asyncio.run(
            run(
                json_path=args.json,
                out_path=args.out,
                max_pages=args.max_pages,
                confidence_threshold=args.confidence,
                concurrency=args.concurrency,
                include_archival=args.include_archival,
                model=args.model,
                limit=args.limit,
                offset=args.offset,
            )
        )
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        sys.exit(130)
    except Exception as exc:  # noqa: BLE001
        print(f"\nPOC failed: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
