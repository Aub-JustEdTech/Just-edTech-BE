#!/usr/bin/env python3
"""
Discover meeting-archive candidate URLs for every school listed in
scripts/school_data/output/selected_schools.json and write the results
to a separate JSON file.

For each school we delegate to `SchoolScraperService.discover_candidate_urls()`,
which walks sitemaps / robots.txt / homepage nav (with an optional
Playwright fallback for JS-rendered sites) and returns the top candidate
URLs whose paths contain meeting-related keywords (meeting, minutes, ...).

Output:
    scripts/school_data/output/school_url_candidates.json
        [
          {
            "name": "Quabbin",
            "org_code": "07530000",
            "website": "https://www.qrsd.org",
            "discovery_method": "wp-sitemap",
            "total_urls_scanned": 123,
            "candidates": [
              {"url": "...", "matched_keywords": ["meeting"], "score": 1},
              ...
            ],
            "error": null
          },
          ...
        ]

Usage:
    python scripts/school_data/discover_school_candidates.py
    python scripts/school_data/discover_school_candidates.py \
        --json path/to/selected_schools.json \
        --out path/to/school_url_candidates.json
    python scripts/school_data/discover_school_candidates.py --use-playwright
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

from app.services.web_scraper.school_scraper_service import SchoolScraperService

DEFAULT_JSON_PATH = Path(__file__).parent / "output" / "selected_schools.json"
DEFAULT_OUT_PATH = Path(__file__).parent / "output" / "school_url_candidates.json"


async def discover_for_school(
    svc: SchoolScraperService, record: dict[str, Any]
) -> dict[str, Any]:
    """Run candidate discovery for one school record."""
    name = (record.get("name") or "").strip()
    org_code = (record.get("org_code") or "").strip()
    website = (record.get("website") or "").strip()

    result: dict[str, Any] = {
        "name": name,
        "org_code": org_code,
        "website": website,
        "discovery_method": None,
        "total_urls_scanned": 0,
        "candidates": [],
        "error": None,
    }

    if not website:
        result["error"] = "missing_website"
        return result

    try:
        discovered = await svc.discover_candidate_urls(website)
    except Exception as exc:  # noqa: BLE001
        result["error"] = f"{type(exc).__name__}: {exc}"
        return result

    result["discovery_method"] = discovered.get("discovery_method")
    result["total_urls_scanned"] = discovered.get("total_urls_scanned", 0)
    result["candidates"] = discovered.get("candidates", [])
    return result


async def run(
    json_path: Path,
    out_path: Path,
    use_playwright: bool,
    concurrency: int,
) -> None:
    if not json_path.exists():
        print(f"Input JSON not found: {json_path}", file=sys.stderr)
        sys.exit(1)

    with json_path.open("r", encoding="utf-8") as f:
        records = json.load(f)

    print("=" * 60)
    print("Just-EdTech School Candidate URL Discovery")
    print(f"  input          : {json_path}")
    print(f"  output         : {out_path}")
    print(f"  schools        : {len(records)}")
    print(f"  use_playwright : {use_playwright}")
    print(f"  concurrency    : {concurrency}")
    print("=" * 60)

    # One shared service per worker so the (expensive) Playwright browser
    # is launched at most `concurrency` times and reused across schools.
    sem = asyncio.Semaphore(concurrency)
    services: list[SchoolScraperService] = [
        SchoolScraperService(use_playwright=use_playwright) for _ in range(concurrency)
    ]

    async def _worker(idx: int, record: dict[str, Any]) -> dict[str, Any]:
        async with sem:
            svc = services[idx % len(services)]
            return await discover_for_school(svc, record)

    tasks = [
        asyncio.create_task(_worker(i, rec), name=rec.get("name", f"school-{i}"))
        for i, rec in enumerate(records)
    ]

    results: list[dict[str, Any]] = []
    for coro in asyncio.as_completed(tasks):
        res = await coro
        results.append(res)
        n_cands = len(res.get("candidates", []))
        err = res.get("error")
        status = "OK " if not err else "ERR"
        print(
            f"  [{status}] {res['name']:<28} "
            f"({res['org_code']}) -> {n_cands} candidates"
            + (f"  ({err})" if err else "")
        )

    # Restore original input order for a stable output file.
    order = {r.get("org_code"): i for i, r in enumerate(records)}
    results.sort(key=lambda r: order.get(r.get("org_code"), 0))

    for svc in services:
        await svc.close()

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)

    total_candidates = sum(len(r.get("candidates", [])) for r in results)
    errors = sum(1 for r in results if r.get("error"))

    print("\n" + "=" * 60)
    print("Summary")
    print("=" * 60)
    print(f"  schools processed : {len(results)}")
    print(f"  errors           : {errors}")
    print(f"  total candidates : {total_candidates}")
    print(f"\nOutput written to: {out_path}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Discover candidate meeting-archive URLs for selected schools."
    )
    parser.add_argument(
        "--json",
        type=Path,
        default=DEFAULT_JSON_PATH,
        help="Path to selected_schools.json.",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=DEFAULT_OUT_PATH,
        help="Path to write the candidate URLs JSON.",
    )
    parser.add_argument(
        "--use-playwright",
        action="store_true",
        help="Force Playwright for JS-rendered sites (else auto-detect).",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=3,
        help="Number of schools to scrape in parallel.",
    )
    args = parser.parse_args()

    try:
        asyncio.run(
            run(
                json_path=args.json,
                out_path=args.out,
                use_playwright=args.use_playwright,
                concurrency=args.concurrency,
            )
        )
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        sys.exit(130)
    except Exception as exc:  # noqa: BLE001
        print(f"\nDiscovery failed: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
