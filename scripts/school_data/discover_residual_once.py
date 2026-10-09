#!/usr/bin/env python3
"""
ONE-SHOT Layer-1 discovery for residual Needs-review districts (prod-first).

Spends LLM tokens **once** against the residual allowlist built after P0
seeding. Do **not** re-run locally and again in prod.

Input (default):
  scripts/school_data/output/residual_discover_once_schools.json
  — school_names minus confirmed batches minus P0 finalised URLs (~146).

Prod runbook (on EC2, after git pull + API memory is healthy):

    # 1) Discover once (LLM). Prefer ranking_mode=llm OR both — pick ONE.
    docker exec just-edtech-api python \\
      scripts/school_data/discover_residual_once.py \\
      --ranking-mode llm \\
      --concurrency 2 \\
      --use-playwright

    # 2) Human-review candidates in the output JSON; build a finalised
    #    scrape-URL file (same shape as p0_finalised_scrape_urls.json).

    # 3) Seed + scrape (Layer 2 — no discovery LLM):
    docker exec just-edtech-api python \\
      scripts/school_data/run_failure_batch_ingest.py \\
      --tenant-id 4 \\
      --json scripts/school_data/output/<your_finalised>.json \\
      --seed-only
    docker exec just-edtech-api python \\
      scripts/school_data/run_scrape_districts.py \\
      --tenant-id 4 \\
      --json scripts/school_data/output/<your_finalised>.json \\
      --concurrency 1

Usage:
    poetry run python scripts/school_data/discover_residual_once.py --ranking-mode keyword
    poetry run python scripts/school_data/discover_residual_once.py --limit 5
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from scripts.school_data.discover_school_candidates import run as discover_run

DEFAULT_JSON = (
    Path(__file__).parent / "output" / "residual_discover_once_schools.json"
)
DEFAULT_OUT = (
    Path(__file__).parent / "output" / "residual_discover_once_candidates.json"
)


async def _run_limited(
    *,
    json_path: Path,
    out_path: Path,
    use_playwright: bool,
    concurrency: int,
    ranking_mode: str,
    limit: int | None,
) -> None:
    import json

    raw = json.loads(json_path.read_text(encoding="utf-8"))
    records = raw["schools"] if isinstance(raw, dict) and "schools" in raw else raw
    if limit is not None:
        records = records[:limit]
        trimmed = json_path.parent / "_residual_discover_once_trimmed.json"
        trimmed.write_text(json.dumps(records, indent=2) + "\n", encoding="utf-8")
        json_path = trimmed

    await discover_run(
        json_path=json_path,
        out_path=out_path,
        use_playwright=use_playwright,
        concurrency=concurrency,
        ranking_mode=ranking_mode,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", type=Path, default=DEFAULT_JSON)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--use-playwright", action="store_true")
    parser.add_argument("--concurrency", type=int, default=2)
    parser.add_argument(
        "--ranking-mode",
        choices=["keyword", "llm", "both"],
        default="llm",
        help="Pick ONE paid mode for prod (default: llm). Do not run twice.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Optional cap for a smoke subset (still counts as a paid pass).",
    )
    args = parser.parse_args()

    try:
        asyncio.run(
            _run_limited(
                json_path=args.json,
                out_path=args.out,
                use_playwright=args.use_playwright,
                concurrency=args.concurrency,
                ranking_mode=args.ranking_mode,
                limit=args.limit,
            )
        )
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        sys.exit(130)
    except Exception as exc:  # noqa: BLE001
        print(f"\nResidual discover failed: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
