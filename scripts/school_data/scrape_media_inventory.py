#!/usr/bin/env python3
"""
Build a media inventory for each district's confirmed meeting-archive URL.

Reads a districts JSON (default: output/finalised_20_disticts.json), scrapes
each `correct_URL`, and writes a report of WHICH file types were found and HOW
MANY of each — per district and in total.

This is read-only reconnaissance: **no database writes, no transcription, no
spend.** Its output is the input to the transcription budget, because the only
unmeasured variable in that budget is how much of the corpus is paid-path
audio/video. Estimates for 500 items span $7 to $173 depending purely on that
mix, so this report replaces a 25x guess with a number.

Scales to 400+ districts:
  - Keys are matched after stripping whitespace and case, so `correct_URL`,
    `correct _URL` and `Correct_Url` all resolve. No hardcoded typos.
  - Results are written incrementally and `--resume` skips districts already
    present in the output, so an interrupted run is not lost.
  - Bounded concurrency, and one district's failure never aborts the run.

Usage:
    python scripts/school_data/scrape_media_inventory.py
    python scripts/school_data/scrape_media_inventory.py --limit 3
    python scripts/school_data/scrape_media_inventory.py --concurrency 5
    python scripts/school_data/scrape_media_inventory.py --org-code 07780000
    python scripts/school_data/scrape_media_inventory.py --resume
    python scripts/school_data/scrape_media_inventory.py \\
        --input scripts/school_data/output/all_400_districts.json \\
        --output scripts/school_data/output/media_inventory_400.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import time
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.services.web_scraper.school_scraper_service import SchoolScraperService

OUTPUT_DIR = Path(__file__).parent / "output"
DEFAULT_INPUT = OUTPUT_DIR / "finalised_20_disticts.json"
DEFAULT_OUTPUT = OUTPUT_DIR / "media_inventory.json"

# A YouTube video has no file extension; bucket it explicitly rather than
# letting it vanish from the extension histogram.
NO_EXTENSION = "(none)"

# Media types that reach the PAID transcription path.
AV_MEDIA_TYPES = ("audio", "video", "youtube")

# Canonical field name -> the whitespace-stripped, lowercased spellings that
# map onto it. Anything not listed is preserved but ignored.
_FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "name": ("name", "district", "districtname", "schoolname"),
    "org_code": ("org_code", "orgcode", "code"),
    "website": ("website", "site", "homepage", "url"),
    "correct_url": (
        "correct_url",
        "correcturl",
        "confirmed_url",
        "confirmedurl",
        "verified_url",
    ),
}


def _normalise_key(key: str) -> str:
    """Strip ALL whitespace and lowercase, so `correct _URL` -> `correct_url`."""
    return re.sub(r"\s+", "", str(key)).lower()


def normalise_record(raw: dict[str, Any]) -> dict[str, Any]:
    """Map a district record onto canonical fields, tolerating key variants.

    Deliberately generic rather than special-casing known typos: the same
    format will arrive for 400 districts and hand-maintained JSON accumulates
    new whitespace and casing mistakes faster than they can be enumerated.
    """
    by_normalised = {_normalise_key(k): v for k, v in raw.items()}

    record: dict[str, Any] = {}
    for canonical, aliases in _FIELD_ALIASES.items():
        value = None
        for alias in aliases:
            candidate = by_normalised.get(alias)
            if candidate is not None and str(candidate).strip():
                value = str(candidate).strip()  # values carry stray spaces too
                break
        record[canonical] = value
    return record


def tally_media(media_files: list[dict]) -> dict[str, Any]:
    """Count discovered files by extension and by media type."""
    by_extension: Counter[str] = Counter()
    by_media_type: Counter[str] = Counter()

    for item in media_files:
        extension = item.get("file_extension") or NO_EXTENSION
        by_extension[str(extension).lower()] += 1
        by_media_type[str(item.get("media_type") or "unknown")] += 1

    av_total = sum(by_media_type.get(t, 0) for t in AV_MEDIA_TYPES)

    return {
        "media_total": len(media_files),
        # The cost driver: only these reach paid transcription.
        "av_total": av_total,
        "document_total": by_media_type.get("document", 0),
        "by_media_type": dict(sorted(by_media_type.items())),
        "by_extension": dict(sorted(by_extension.items())),
    }


async def scrape_one(record: dict[str, Any], crawl_depth: int) -> dict[str, Any]:
    """Scrape a single district. Never raises — failures are recorded."""
    result: dict[str, Any] = {
        "name": record.get("name"),
        "org_code": record.get("org_code"),
        "website": record.get("website"),
        "correct_url": record.get("correct_url"),
        "status": "pending",
        "pages_crawled": 0,
        "media_total": 0,
        "av_total": 0,
        "document_total": 0,
        "by_media_type": {},
        "by_extension": {},
        "elapsed_seconds": 0.0,
        "error": None,
    }

    url = record.get("correct_url")
    if not url:
        result["status"] = "missing_url"
        result["error"] = "no correct_URL field found in the input record"
        return result

    started = time.monotonic()
    try:
        async with SchoolScraperService() as scraper:
            scraped = await scraper.scrape_media_files(
                page_url=url,
                crawl_depth=crawl_depth,
            )
        media_files = scraped.get("media_files", [])
        result.update(tally_media(media_files))
        result["pages_crawled"] = scraped.get("pages_crawled", 0)
        result["status"] = "ok"
    except Exception as exc:  # noqa: BLE001 — one bad site must not stop 399
        result["status"] = "failed"
        result["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        result["elapsed_seconds"] = round(time.monotonic() - started, 1)

    return result


def find_duplicate_urls(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Districts sharing one URL — they will each pay for the same media.

    Dedup in `scraped_media` is per school (`school_id`, `content_hash`), so a
    shared archive page is transcribed once per district, not once overall.
    Known case: Shutesbury and New Salem-Wendell both point at union28.org.
    """
    by_url: dict[str, list[str]] = {}
    for record in records:
        url = record.get("correct_url")
        if not url:
            continue
        by_url.setdefault(url, []).append(record.get("name") or "?")

    return [
        {"url": url, "districts": names, "count": len(names)}
        for url, names in sorted(by_url.items())
        if len(names) > 1
    ]


def build_summary(
    districts: list[dict[str, Any]],
    duplicates: list[dict[str, Any]],
) -> dict[str, Any]:
    by_extension: Counter[str] = Counter()
    by_media_type: Counter[str] = Counter()

    for district in districts:
        by_extension.update(district.get("by_extension") or {})
        by_media_type.update(district.get("by_media_type") or {})

    statuses = Counter(d.get("status") for d in districts)
    av_total = sum(by_media_type.get(t, 0) for t in AV_MEDIA_TYPES)

    return {
        "districts_total": len(districts),
        "districts_ok": statuses.get("ok", 0),
        "districts_failed": statuses.get("failed", 0),
        "districts_missing_url": statuses.get("missing_url", 0),
        "districts_with_zero_media": sum(
            1 for d in districts if d.get("status") == "ok" and not d.get("media_total")
        ),
        "media_total": sum(d.get("media_total", 0) for d in districts),
        "av_total": av_total,
        "document_total": by_media_type.get("document", 0),
        "by_media_type": dict(sorted(by_media_type.items())),
        "by_extension": dict(sorted(by_extension.items())),
        "duplicate_urls": duplicates,
    }


def write_report(
    output_path: Path,
    input_path: Path,
    crawl_depth: int,
    districts: list[dict[str, Any]],
    duplicates: list[dict[str, Any]],
) -> None:
    """Write the report. Called after every district so a crash keeps progress."""
    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "input_file": str(input_path.name),
        "crawl_depth": crawl_depth,
        "note": (
            "Read-only reconnaissance. No database writes, no transcription, "
            "no cost. av_total is the paid-transcription driver."
        ),
        "summary": build_summary(districts, duplicates),
        "districts": districts,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = output_path.with_suffix(output_path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    # Atomic replace so an interrupted write never truncates a good report.
    tmp.replace(output_path)


def load_existing(output_path: Path) -> dict[str, dict[str, Any]]:
    """Existing results keyed by org_code, for --resume."""
    if not output_path.exists():
        return {}
    try:
        with output_path.open("r", encoding="utf-8") as fh:
            payload = json.load(fh)
    except (json.JSONDecodeError, OSError):
        return {}
    return {
        d["org_code"]: d
        for d in payload.get("districts", [])
        # Only completed work is reusable; retry failures on the next run.
        if d.get("org_code") and d.get("status") == "ok"
    }


async def run(args: argparse.Namespace) -> int:
    input_path = Path(args.input)
    output_path = Path(args.output)

    if not input_path.exists():
        print(f"ERROR: input not found: {input_path}", file=sys.stderr)
        return 1

    with input_path.open("r", encoding="utf-8") as fh:
        raw_records = json.load(fh)

    records = [normalise_record(r) for r in raw_records]

    if args.org_code:
        records = [r for r in records if r.get("org_code") == args.org_code]
    if args.limit:
        records = records[: args.limit]

    duplicates = find_duplicate_urls(records)
    already_done = load_existing(output_path) if args.resume else {}

    print("=" * 68)
    print("Just-EdTech Media Inventory (read-only — no DB writes, no cost)")
    print(f"  input       : {input_path}")
    print(f"  output      : {output_path}")
    print(f"  districts   : {len(records)}")
    print(f"  crawl_depth : {args.crawl_depth}")
    print(f"  concurrency : {args.concurrency}")
    if already_done:
        print(f"  resuming    : {len(already_done)} already complete, will skip")
    if duplicates:
        print(f"  ⚠ shared URLs: {len(duplicates)} (each district pays separately)")
        for dup in duplicates:
            print(f"      {', '.join(dup['districts'])}  ->  {dup['url']}")
    print("=" * 68)

    results: list[dict[str, Any]] = []
    semaphore = asyncio.Semaphore(args.concurrency)
    completed = 0
    total = len(records)
    lock = asyncio.Lock()

    async def worker(record: dict[str, Any]) -> dict[str, Any]:
        nonlocal completed

        org_code = record.get("org_code")
        if org_code and org_code in already_done:
            result = already_done[org_code]
        else:
            async with semaphore:
                result = await scrape_one(record, args.crawl_depth)

        async with lock:
            completed += 1
            results.append(result)
            flag = {"ok": "✓", "failed": "✗", "missing_url": "?"}.get(
                result["status"], "·"
            )
            print(
                f"[{completed:>3}/{total}] {flag} {str(result['name'])[:34]:<34} "
                f"media={result['media_total']:<4} av={result['av_total']:<3} "
                f"pages={result['pages_crawled']:<3} {result['elapsed_seconds']}s"
                + (f"  {result['error']}" if result.get("error") else "")
            )
            # Written every district, so an interrupted 400-run keeps its work.
            write_report(
                output_path, input_path, args.crawl_depth, results, duplicates
            )

        return result

    await asyncio.gather(*(worker(r) for r in records))

    # Restore input order — completion order is nondeterministic.
    order = {r.get("org_code"): i for i, r in enumerate(records)}
    results.sort(key=lambda d: order.get(d.get("org_code"), 0))
    write_report(output_path, input_path, args.crawl_depth, results, duplicates)

    summary = build_summary(results, duplicates)
    print("=" * 68)
    print(f"  ok / failed / no-url : {summary['districts_ok']} / "
          f"{summary['districts_failed']} / {summary['districts_missing_url']}")
    print(f"  districts with 0 media: {summary['districts_with_zero_media']}")
    print(f"  media found          : {summary['media_total']}")
    print(f"  by type              : {summary['by_media_type']}")
    print(f"  by extension         : {summary['by_extension']}")
    print(f"  PAID-PATH items (a/v): {summary['av_total']}")
    print(f"  report               : {output_path}")
    print("=" * 68)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Inventory media types and counts per district (read-only)."
    )
    parser.add_argument("--input", default=str(DEFAULT_INPUT))
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    parser.add_argument(
        "--crawl-depth",
        type=int,
        default=1,
        help="Sub-page depth to follow, 0-3 (default 1).",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=3,
        help="Districts scraped in parallel. Each may launch its own headless "
        "Chromium, so raise this cautiously (default 3).",
    )
    parser.add_argument(
        "--limit", type=int, default=None, help="Only the first N districts."
    )
    parser.add_argument(
        "--org-code", default=None, help="Scrape a single district by org_code."
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Skip districts already recorded as ok in the output file.",
    )
    args = parser.parse_args()

    args.crawl_depth = max(0, min(args.crawl_depth, 3))
    args.concurrency = max(1, args.concurrency)

    return asyncio.run(run(args))


if __name__ == "__main__":
    sys.exit(main())
