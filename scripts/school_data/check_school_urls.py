#!/usr/bin/env python3
"""
Check the reachability of every school website listed in
scripts/school_data/output/school_names.json and write the schools whose URL
is missing or unreachable to a report file.

For each school record we:
    1. Skip it (and add to the "bad" list) if it has no `website` value.
    2. Issue an HTTP HEAD (falling back to GET) request with a short timeout.
    3. Treat HTTP 2xx/3xx as reachable; everything else (DNS failure,
       connection error, timeout, 4xx/5xx, SSL error, ...) as unreachable.

Outputs:
    scripts/school_data/output/unreachable_schools.json   - structured report
    scripts/school_data/output/unreachable_schools.csv    - same data as CSV
    A summary is printed to stdout.

Usage:
    python scripts/school_data/check_school_urls.py
    python scripts/school_data/check_school_urls.py --json path/to/school_names.json
    python scripts/school_data/check_school_urls.py --timeout 10 --workers 20
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests

DEFAULT_JSON_PATH = Path(__file__).parent / "output" / "school_names.json"
DEFAULT_OUT_JSON = Path(__file__).parent / "output" / "unreachable_schools.json"
DEFAULT_OUT_CSV = Path(__file__).parent / "output" / "unreachable_schools.csv"

# A desktop-ish UA - some school sites block default python-requests UA.
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
}


def _is_valid_url(url: str) -> bool:
    if not url:
        return False
    parsed = urlparse(url.strip())
    return parsed.scheme in ("http", "https") and bool(parsed.netloc)


def check_one(record: dict[str, Any], timeout: float) -> dict[str, Any]:
    """Return a result dict describing whether the school's URL is reachable."""
    name = (record.get("name") or "").strip()
    org_code = (record.get("org_code") or "").strip()
    website = (record.get("website") or "").strip()

    result: dict[str, Any] = {
        "number": record.get("number"),
        "name": name,
        "org_code": org_code,
        "district_type": record.get("district_type"),
        "website": website,
        "status": None,
        "reason": None,
    }

    if not website:
        result["reason"] = "missing_url"
        return result

    if not _is_valid_url(website):
        result["reason"] = "invalid_url"
        return result

    try:
        # Try HEAD first (lighter); some servers reject HEAD, so fall back to GET.
        try:
            resp = requests.head(
                website,
                headers=HEADERS,
                timeout=timeout,
                allow_redirects=True,
            )
            method = "HEAD"
            if resp.status_code >= 400 or resp.status_code == 405:
                raise requests.RequestException(f"status {resp.status_code}")
        except requests.RequestException:
            resp = requests.get(
                website,
                headers=HEADERS,
                timeout=timeout,
                allow_redirects=True,
                stream=True,
            )
            method = "GET"
            # consume & close immediately
            resp.close()

        result["status"] = resp.status_code
        if 200 <= resp.status_code < 400:
            result["reachable"] = True
            result["method"] = method
            return result
        result["reason"] = f"http_{resp.status_code}"
        return result
    except requests.exceptions.SSLError as exc:
        result["reason"] = "ssl_error"
        result["error"] = str(exc)
        return result
    except requests.exceptions.ConnectionError as exc:
        result["reason"] = "connection_error"
        result["error"] = str(exc)
        return result
    except requests.exceptions.Timeout as exc:
        result["reason"] = "timeout"
        result["error"] = str(exc)
        return result
    except requests.exceptions.RequestException as exc:
        result["reason"] = "request_error"
        result["error"] = str(exc)
        return result
    except Exception as exc:  # noqa: BLE001
        result["reason"] = "unknown_error"
        result["error"] = str(exc)
        return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Check school website URLs.")
    parser.add_argument("--json", type=Path, default=DEFAULT_JSON_PATH,
                        help="Path to school_names.json.")
    parser.add_argument("--out-json", type=Path, default=DEFAULT_OUT_JSON,
                        help="Path to write the unreachable report JSON.")
    parser.add_argument("--out-csv", type=Path, default=DEFAULT_OUT_CSV,
                        help="Path to write the unreachable report CSV.")
    parser.add_argument("--timeout", type=float, default=10.0,
                        help="Per-request timeout in seconds.")
    parser.add_argument("--workers", type=int, default=20,
                        help="Number of concurrent workers.")
    parser.add_argument("--all", action="store_true",
                        help="Write every school (reachable + unreachable) to the JSON report.")
    args = parser.parse_args()

    if not args.json.exists():
        print(f"Input JSON not found: {args.json}", file=sys.stderr)
        sys.exit(1)

    with args.json.open("r", encoding="utf-8") as f:
        records = json.load(f)

    print("=" * 60)
    print("Just-EdTech School URL Checker")
    print(f"  input    : {args.json}")
    print(f"  schools  : {len(records)}")
    print(f"  timeout  : {args.timeout}s")
    print(f"  workers  : {args.workers}")
    print("=" * 60)

    total = len(records)
    done = 0
    unreachable: list[dict[str, Any]] = []
    all_results: list[dict[str, Any]] = []

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(check_one, rec, args.timeout): rec for rec in records}
        for fut in as_completed(futures):
            res = fut.result()
            done += 1
            if args.all:
                all_results.append(res)
            is_ok = res.get("reachable", False) is True
            if not is_ok:
                unreachable.append(res)
            if done % 25 == 0 or done == total:
                print(f"  checked {done}/{total} ... unreachable so far: {len(unreachable)}")

    # Sort unreachable by number for stable output.
    unreachable.sort(key=lambda r: (r.get("number") is None, r.get("number") or 0))

    # Group by reason for the summary.
    by_reason: dict[str, int] = {}
    for r in unreachable:
        key = r.get("reason") or "unknown"
        by_reason[key] = by_reason.get(key, 0) + 1

    # Write JSON report.
    report = {
        "input": str(args.json),
        "total": total,
        "reachable": total - len(unreachable),
        "unreachable_count": len(unreachable),
        "by_reason": by_reason,
        "unreachable": unreachable,
    }
    if args.all:
        report["all"] = all_results

    args.out_json.parent.mkdir(parents=True, exist_ok=True)
    with args.out_json.open("w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)

    # Write CSV report.
    fieldnames = ["number", "name", "org_code", "district_type",
                  "website", "status", "reason", "error"]
    with args.out_csv.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for r in unreachable:
            writer.writerow(r)

    print("\n" + "=" * 60)
    print("Summary")
    print("=" * 60)
    print(f"  total schools   : {total}")
    print(f"  reachable       : {total - len(unreachable)}")
    print(f"  unreachable     : {len(unreachable)}")
    print("\nBreakdown by reason:")
    for reason, count in sorted(by_reason.items(), key=lambda kv: kv[1], reverse=True):
        print(f"  {reason:<20}: {count}")
    print(f"\nReports written:")
    print(f"  JSON : {args.out_json}")
    print(f"  CSV  : {args.out_csv}")


if __name__ == "__main__":
    main()
