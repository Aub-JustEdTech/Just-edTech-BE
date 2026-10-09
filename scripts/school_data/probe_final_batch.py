#!/usr/bin/env python3
"""
Dry-run probe for finalised school URL batches.

For each school in a batch JSON, detects the hosting platform per URL and
runs a shallow scrape_media_files probe at depth 0 or 1 (mapped from
``clicks needed``: 1 → depth 0, 2 → depth 1).

Usage:
    python scripts/school_data/probe_final_batch.py
    python scripts/school_data/probe_final_batch.py --limit 5
    python scripts/school_data/probe_final_batch.py \\
        --json "scripts/school_data/output/final batch 1.json" \\
        --out "scripts/school_data/output/final batch 1 dry check.json"
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from urllib.parse import urlparse

import httpx

from app.services.web_scraper._discovery_helpers import html_needs_playwright
from app.services.web_scraper.board_platforms import board_platform_kind
from app.services.web_scraper.school_scraper_service import SchoolScraperService

DEFAULT_JSON = Path(__file__).parent / "output" / "final batch 1.json"
DEFAULT_OUT = Path(__file__).parent / "output" / "final batch 1 dry check.json"

_URL_PLATFORM_HINTS: tuple[tuple[str, str], ...] = (
    ("schoolblocks", "schoolblocks.com"),
    ("documents-on-demand", "documents-on-demand.com"),
    ("schoolpointe", "schoolpointe.net"),
    ("google-drive", "drive.google.com"),
    ("google-docs", "docs.google.com"),
    ("sharepoint", "sharepoint.com"),
    ("boardbook", "boardbook.org"),
    ("municode", "municode.com"),
    ("civicclerk", "civicclerk.com"),
    ("legistar", "legistar.com"),
    ("wordpress", "wp-content"),
)

_HTML_PLATFORM_HINTS: tuple[tuple[str, str], ...] = (
    ("finalsite", "finalsitestatic.com"),
    ("apptegy", "apptegy.net"),
    ("apptegy", "thrillshare.com"),
    ("catapult", "catapultcms.com"),
    ("catapult", "ccms_documentlinklisting"),
    ("sharpschool", "sharpschool.com"),
    ("sharpschool", 'id="documentList"'),
    ("eschool", "eschoolsolutions.com"),
    ("blackboard", "blackboard.com/"),
    ("granicus_embed", "granicus.com"),
    ("nextjs", 'id="__next"'),
    ("nuxt", 'id="__nuxt"'),
)


def _detect_platform(url: str, html: str | None) -> str:
    kind = board_platform_kind(url)
    if kind:
        return kind

    lowered = url.lower()
    host = (urlparse(url).hostname or "").lower()
    for name, pattern in _URL_PLATFORM_HINTS:
        if pattern in host or pattern in lowered:
            return name

    if html:
        for name, signal in _HTML_PLATFORM_HINTS:
            if signal in html:
                return name
        if html_needs_playwright(html):
            return "js_cms"

    return "generic"


async def _fetch_html_snippet(url: str, timeout: float) -> str | None:
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
        )
    }
    try:
        async with httpx.AsyncClient(
            follow_redirects=True, timeout=timeout, headers=headers
        ) as client:
            resp = await client.get(url)
            if resp.status_code >= 400:
                return None
            return resp.text[:500_000]
    except Exception:  # noqa: BLE001
        return None


async def _probe_one_url(
    url: str,
    crawl_depth: int,
    timeout: int,
    html_cache: dict[str, str | None],
) -> dict:
    if url not in html_cache:
        html_cache[url] = await _fetch_html_snippet(url, timeout)

    html = html_cache[url]
    platform = _detect_platform(url, html)
    use_playwright = platform in {
        "diligent",
        "boardontrack",
        "boarddocs",
        "granicus",
        "granicus_embed",
        "js_cms",
        "finalsite",
        "apptegy",
        "catapult",
        "sharpschool",
    }

    result = {
        "url": url,
        "detected platform": platform,
        "probe_depth": crawl_depth,
        "media_found": False,
        "media_count": 0,
        "pages_crawled": 0,
        "error": None,
    }

    try:
        async with SchoolScraperService(timeout=timeout, use_playwright=use_playwright) as svc:
            scrape = await svc.scrape_media_files(page_url=url, crawl_depth=crawl_depth)
    except Exception as exc:  # noqa: BLE001
        result["error"] = str(exc)
        return result

    media_files = scrape.get("media_files") or []
    result["pages_crawled"] = int(scrape.get("pages_crawled") or 0)
    result["media_count"] = len(media_files)
    result["media_found"] = len(media_files) > 0
    type_counts: dict[str, int] = {}
    samples: dict[str, list[str]] = {}
    for mf in media_files:
        mt = str(mf.get("media_type") or "unknown")
        type_counts[mt] = type_counts.get(mt, 0) + 1
        url = str(mf.get("url") or "")
        if url and len(samples.get(mt, [])) < 2:
            samples.setdefault(mt, []).append(url)
    result["media_type_counts"] = type_counts
    result["media_type_samples"] = samples
    return result


async def probe_batch(
    json_path: Path,
    out_path: Path,
    *,
    limit: int | None,
    concurrency: int,
    timeout: int,
) -> dict:
    records = json.loads(json_path.read_text(encoding="utf-8"))
    if limit is not None:
        records = records[:limit]

    sem = asyncio.Semaphore(concurrency)
    html_cache: dict[str, str | None] = {}
    results: list[dict | None] = [None] * len(records)
    done = 0

    async def _process(idx: int, rec: dict) -> None:
        nonlocal done
        async with sem:
            name = rec.get("School name", "")
            clicks = int(rec.get("clicks needed") or 2)
            probe_depth = max(0, clicks - 1)
            urls = list(rec.get("finalised urls") or [])

            url_probes: list[dict] = []
            for url in urls:
                url_probes.append(
                    await _probe_one_url(url, probe_depth, timeout, html_cache)
                )

            platforms = sorted({p["detected platform"] for p in url_probes})
            any_media = any(p["media_found"] for p in url_probes)
            total_media = sum(p["media_count"] for p in url_probes)

            enriched = {
                **rec,
                "detected platform": platforms[0] if len(platforms) == 1 else platforms,
                "probe_depth": probe_depth,
                "probe media found": any_media,
                "probe media count": total_media,
                "url probes": url_probes,
            }
            results[idx] = enriched
            done += 1
            status = "media" if any_media else "empty"
            type_totals: dict[str, int] = {}
            for p in url_probes:
                for mt, count in (p.get("media_type_counts") or {}).items():
                    type_totals[mt] = type_totals.get(mt, 0) + count
            type_summary = (
                ", ".join(f"{k}={v}" for k, v in sorted(type_totals.items()))
                if type_totals
                else "none"
            )
            err_count = sum(1 for p in url_probes if p.get("error"))
            print(
                f"[{done}/{len(records)}] {name} — "
                f"platform={enriched['detected platform']} "
                f"depth={probe_depth} {status} ({total_media} files: {type_summary})"
                f"{f' errors={err_count}' if err_count else ''}",
                flush=True,
            )

            if done % 5 == 0 or done == len(records):
                out_path.write_text(
                    json.dumps([r for r in results if r is not None], indent=2, ensure_ascii=False)
                    + "\n",
                    encoding="utf-8",
                )

    await asyncio.gather(*[_process(i, rec) for i, rec in enumerate(records)])

    final = [r for r in results if r is not None]
    out_path.write_text(
        json.dumps(final, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    summary = {
        "schools": len(final),
        "with_media": sum(1 for r in final if r["probe media found"]),
        "without_media": sum(1 for r in final if not r["probe media found"]),
        "errors": sum(
            1 for r in final for p in r["url probes"] if p.get("error")
        ),
    }
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Probe finalised URL batches.")
    parser.add_argument("--json", type=Path, default=DEFAULT_JSON)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--concurrency", type=int, default=3)
    parser.add_argument("--timeout", type=int, default=45)
    args = parser.parse_args()

    if not args.json.exists():
        print(f"Input not found: {args.json}", file=sys.stderr)
        sys.exit(1)

    print("=" * 60)
    print("Final batch URL probe (dry check)")
    print(f"  input       : {args.json}")
    print(f"  output      : {args.out}")
    print(f"  limit       : {args.limit or 'all'}")
    print(f"  concurrency : {args.concurrency}")
    print("=" * 60)

    summary = asyncio.run(
        probe_batch(
            args.json,
            args.out,
            limit=args.limit,
            concurrency=args.concurrency,
            timeout=args.timeout,
        )
    )
    print("\nSummary:")
    for k, v in summary.items():
        print(f"  {k}: {v}")
    print(f"\nWrote {args.out}")


if __name__ == "__main__":
    main()
