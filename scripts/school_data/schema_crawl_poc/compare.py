#!/usr/bin/env python3
"""
Compare schema-driven POC output against the keyword-based baseline.

Reads:
  - scripts/school_data/output/school_url_candidates.json   (keyword baseline)
  - scripts/school_data/output/schema_crawl_results.json    (schema-driven POC)

Writes a human-readable comparison to stdout and an optional JSON report.

Per-school metrics:
  - n_keyword_candidates   : # candidate URLs the keyword scorer returned
  - n_schema_data_pages    : # data pages the schema crawler found
  - n_schema_archival      : # of those marked is_archive=true (skipped by default)
  - overlap               : # schema data-page URLs that also appear in keyword candidates
  - schema_only           : # data pages found only by the schema crawler
  - keyword_only          : # keyword candidates NOT surfaced by the schema crawler
  - llm_calls             : # LLM calls the schema crawler made for that school

Usage:
    python -m scripts.school_data.schema_crawl_poc.compare \\
        --keyword scripts/school_data/output/school_url_candidates.json \\
        --schema  scripts/school_data/output/schema_crawl_results.json \\
        --out    scripts/school_data/output/schema_vs_keyword.md
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

DEFAULT_KEYWORD = (
    Path(__file__).resolve().parents[1] / "output" / "school_url_candidates.json"
)
DEFAULT_SCHEMA = (
    Path(__file__).resolve().parents[1] / "output" / "schema_crawl_results.json"
)
DEFAULT_OUT = Path(__file__).resolve().parents[1] / "output" / "schema_vs_keyword.md"


def _normalize(url: str) -> str:
    return url.rstrip("/").split("#", 1)[0]


def compare(
    keyword_records: list[dict[str, Any]],
    schema_records: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    by_org_keyword = {(r.get("org_code") or "").strip(): r for r in keyword_records}
    rows: list[dict[str, Any]] = []
    totals: Counter[str] = Counter()

    for srec in schema_records:
        org = (srec.get("org_code") or "").strip()
        name = srec.get("name", "")
        krec = by_org_keyword.get(org, {})

        keyword_urls = {
            _normalize(c["url"]) for c in (krec.get("candidates") or []) if c.get("url")
        }
        data_pages = srec.get("data_pages") or []
        schema_urls = {_normalize(p["url"]) for p in data_pages if p.get("url")}
        archival = [
            p for p in data_pages if (p.get("data_page_info") or {}).get("is_archive")
        ]

        overlap = keyword_urls & schema_urls
        schema_only = schema_urls - keyword_urls
        keyword_only = keyword_urls - schema_urls

        row = {
            "name": name,
            "org_code": org,
            "n_keyword_candidates": len(keyword_urls),
            "n_schema_data_pages": len(schema_urls),
            "n_schema_archival": len(archival),
            "overlap": len(overlap),
            "schema_only": len(schema_only),
            "keyword_only": len(keyword_only),
            "llm_calls": srec.get("llm_calls", 0),
            "schema_data_types": sorted(
                {(p.get("data_page_info") or {}).get("data_type") for p in data_pages}
                - {None}
            ),
            "schema_only_urls": sorted(schema_only),
            "keyword_only_urls": sorted(keyword_only),
        }
        rows.append(row)

        totals["schools"] += 1
        totals["keyword_candidates"] += len(keyword_urls)
        totals["schema_data_pages"] += len(schema_urls)
        totals["schema_archival"] += len(archival)
        totals["overlap"] += len(overlap)
        totals["schema_only"] += len(schema_only)
        totals["keyword_only"] += len(keyword_only)
        totals["llm_calls"] += srec.get("llm_calls", 0)

    return rows, dict(totals)


def render_markdown(rows: list[dict[str, Any]], totals: dict[str, int]) -> str:
    lines: list[str] = []
    lines.append("# Schema-Driven vs Keyword-Based Discovery Comparison")
    lines.append("")
    lines.append("## Totals")
    lines.append("")
    lines.append("| Metric | Value |")
    lines.append("|---|---|")
    for k, v in totals.items():
        lines.append(f"| {k} | {v} |")
    lines.append("")
    lines.append("## Per-school")
    lines.append("")
    lines.append(
        "| School | Org | KW cands | Schema data | Archival | Overlap | Schema-only | KW-only | LLM calls | Types |"
    )
    lines.append("|---|---|---|---|---|---|---|---|---|---|")
    for r in rows:
        lines.append(
            f"| {r['name']} | {r['org_code']} "
            f"| {r['n_keyword_candidates']} | {r['n_schema_data_pages']} "
            f"| {r['n_schema_archival']} | {r['overlap']} "
            f"| {r['schema_only']} | {r['keyword_only']} "
            f"| {r['llm_calls']} | {', '.join(r['schema_data_types']) or '-'} |"
        )
    lines.append("")
    lines.append("## Schema-only discoveries (URLs the keyword scorer missed)")
    lines.append("")
    for r in rows:
        if not r["schema_only_urls"]:
            continue
        lines.append(f"### {r['name']} ({r['org_code']})")
        for u in r["schema_only_urls"]:
            lines.append(f"- {u}")
        lines.append("")
    lines.append("## Keyword-only candidates (not surfaced by the schema crawler)")
    lines.append("")
    for r in rows:
        if not r["keyword_only_urls"]:
            continue
        lines.append(f"### {r['name']} ({r['org_code']})")
        for u in r["keyword_only_urls"]:
            lines.append(f"- {u}")
        lines.append("")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Compare schema-driven POC output vs the keyword baseline."
    )
    parser.add_argument("--keyword", type=Path, default=DEFAULT_KEYWORD)
    parser.add_argument("--schema", type=Path, default=DEFAULT_SCHEMA)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()

    for path in (args.keyword, args.schema):
        if not path.exists():
            print(f"Missing input: {path}", file=sys.stderr)
            sys.exit(1)

    keyword_records = json.loads(args.keyword.read_text(encoding="utf-8"))
    schema_records = json.loads(args.schema.read_text(encoding="utf-8"))

    rows, totals = compare(keyword_records, schema_records)
    md = render_markdown(rows, totals)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(md, encoding="utf-8")

    print(md)
    print(f"\nReport written to: {args.out}", file=sys.stderr)


if __name__ == "__main__":
    main()
