#!/usr/bin/env python3
"""
Confirm a SchoolScrapeUrl for each of the 20 districts listed in
scripts/school_data/output/selected_schools_url_candidates_both.json.

For each school:
  - Skip if the school already has one or more active SchoolScrapeUrl rows.
  - Otherwise pick the best candidate from the discovery JSON using a
    conservative heuristic (data_type=board_minutes|board_agenda, score>=50,
    years intersecting the 2024-2026 window when known, no URL fragment).
  - Create the SchoolScrapeUrl via app.crud.schools.add_scrape_url (idempotent
    on (school_id, url)).

Idempotent: safe to re-run. Existing scrape URLs are left untouched.

Usage:
    python scripts/school_data/confirm_scrape_urls.py
    python scripts/school_data/confirm_scrape_urls.py --dry-run
    python scripts/school_data/confirm_scrape_urls.py \\
        --json path/to/selected_schools_url_candidates_both.json
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
from app.models.school import School
from app.schemas.schools import ScrapeUrlCreate

DEFAULT_JSON_PATH = (
    Path(__file__).parent
    / "output"
    / "selected_schools_url_candidates_both.json"
)

ALLOWED_YEARS = set(settings.SCHOOL_SCRAPER_ALLOWED_YEARS)
PREFERRED_DATA_TYPES = {"board_minutes", "board_agenda"}


def _has_fragment(url: str) -> bool:
    return "#" in url


def _looks_wrong_type(url: str) -> bool:
    """Reject URLs that are clearly not meeting-minutes archives.

    `/apps/video/` (SchoolPointe video portal) and similar paths surface in
    discovery results because they match the `video` keyword, but they are
    not where board minutes PDFs live. Skip them so we don't seed a wrong
    scrape URL that the biweekly cycle would crawl fruitlessly.

    Also reject direct-document URLs (ending in `.pdf`, `.docx`, etc.) —
    a scrape URL should point at an index/listing page that the scraper
    walks to discover many documents, not at a single document.
    """
    lowered = url.lower()
    if "/apps/video/" in lowered or lowered.endswith("/apps/video"):
        return True
    # Reject direct-document URLs. The scraper is meant to walk a listing
    # page and discover documents from it, not ingest a single hardcoded PDF.
    doc_exts = (
        ".pdf", ".docx", ".doc", ".xlsx", ".xls", ".pptx", ".ppt",
    )
    if lowered.endswith(doc_exts):
        return True
    return False


def _years_intersect(cand: dict) -> bool:
    years = cand.get("data_years_available") or []
    if not years:
        # Unknown years — do not penalize.
        return True
    return bool(set(years) & ALLOWED_YEARS)


def pick_best_candidate(candidates: list[dict]) -> dict | None:
    """Pick the best candidate URL to confirm as the school's scrape URL.

    Heuristic (in priority order):
      1. data_type in {board_minutes, board_agenda} AND score >= 50 AND
         years intersect allowed set (when known) AND no URL fragment.
      2. data_type in {board_minutes, board_agenda} AND score >= 50 AND
         no URL fragment (years unknown).
      3. data_type in {board_minutes, board_agenda} AND no URL fragment
         (any score).
      4. score >= 50 AND no URL fragment (data_type unknown/other).
      5. Highest-score candidate with no URL fragment.
      6. First candidate overall (last resort).
    """
    if not candidates:
        return None

    def is_preferred(c: dict) -> bool:
        return c.get("data_type") in PREFERRED_DATA_TYPES

    def no_fragment(c: dict) -> bool:
        return not _has_fragment(c.get("url", ""))

    def not_wrong_type(c: dict) -> bool:
        return not _looks_wrong_type(c.get("url", ""))

    # Each filter is applied as a pass; the first non-empty pass wins.
    # `not_wrong_type` is a hard requirement in every pass.
    passes = [
        lambda c: is_preferred(c) and c.get("score", 0) >= 50
        and _years_intersect(c) and no_fragment(c) and not_wrong_type(c),
        lambda c: is_preferred(c) and c.get("score", 0) >= 50
        and no_fragment(c) and not_wrong_type(c),
        lambda c: is_preferred(c) and no_fragment(c) and not_wrong_type(c),
        lambda c: c.get("score", 0) >= 50 and no_fragment(c) and not_wrong_type(c),
        lambda c: no_fragment(c) and not_wrong_type(c),
    ]

    for predicate in passes:
        matched = [c for c in candidates if predicate(c)]
        if matched:
            # Within a pass, sort by score desc, then prefer ones with known
            # years intersecting the allowed set.
            matched.sort(
                key=lambda c: (
                    c.get("score", 0),
                    _years_intersect(c),
                ),
                reverse=True,
            )
            return matched[0]

    # No candidate passed even the loosest filter — return None so the
    # caller skips this school rather than seeding a wrong scrape URL.
    # The school will need manual re-discovery later.
    return None


async def confirm_scrape_urls(json_path: Path, dry_run: bool) -> dict:
    print("=" * 60)
    print("Just-EdTech Scrape-URL Confirmer")
    print(f"  source   : {json_path}")
    print(f"  dry_run  : {dry_run}")
    print(f"  years    : {sorted(ALLOWED_YEARS)}")
    print("=" * 60)

    if not json_path.exists():
        raise FileNotFoundError(f"Candidates JSON not found: {json_path}")

    with json_path.open("r", encoding="utf-8") as f:
        schools_data = json.load(f)

    stats = {
        "total": len(schools_data),
        "already_set": 0,
        "confirmed": 0,
        "no_candidates": 0,
        "school_not_found": 0,
    }
    decisions: list[dict] = []

    async with AsyncSessionLocal() as db:
        for rec in schools_data:
            org_code = rec.get("org_code", "").strip()
            name = rec.get("name", "").strip()
            candidates = rec.get("candidates") or []

            school = (
                await db.execute(
                    select(School)
                    .where(School.org_code == org_code)
                    .options(selectinload(School.scrape_urls))
                )
            ).scalar_one_or_none()

            if not school:
                print(f"  [skip] {name} ({org_code}) — school not in DB")
                stats["school_not_found"] += 1
                decisions.append(
                    {"org_code": org_code, "name": name, "action": "school_not_found"}
                )
                continue

            active_urls = [
                su for su in (school.scrape_urls or []) if su.is_active
            ]
            if active_urls:
                print(
                    f"  [skip] {name} ({org_code}) — "
                    f"already has {len(active_urls)} URL(s)"
                )
                stats["already_set"] += 1
                decisions.append(
                    {
                        "org_code": org_code,
                        "name": name,
                        "action": "already_set",
                        "urls": [su.url for su in active_urls],
                    }
                )
                continue

            best = pick_best_candidate(candidates)
            if not best:
                print(f"  [skip] {name} ({org_code}) — no candidates in JSON")
                stats["no_candidates"] += 1
                decisions.append(
                    {"org_code": org_code, "name": name, "action": "no_candidates"}
                )
                continue

            url = best["url"]
            print(
                f"  [pick] {name} ({org_code}) — "
                f"score={best.get('score')} "
                f"data_type={best.get('data_type')} "
                f"years={best.get('data_years_available')}"
            )
            print(f"          url: {url}")

            if dry_run:
                decisions.append(
                    {
                        "org_code": org_code,
                        "name": name,
                        "action": "would_confirm",
                        "url": url,
                        "score": best.get("score"),
                        "data_type": best.get("data_type"),
                    }
                )
                continue

            data = ScrapeUrlCreate(
                url=url,
                crawl_depth=1,
                use_playwright=False,
            )
            scrape_url = await crud.add_scrape_url(
                db, school, data, user_id=None
            )
            stats["confirmed"] += 1
            decisions.append(
                {
                    "org_code": org_code,
                    "name": name,
                    "action": "confirmed",
                    "url": url,
                    "scrape_url_id": scrape_url.id,
                }
            )

    print("\nConfirm results:")
    for k, v in stats.items():
        print(f"  {k:<20}: {v}")
    print("\nDone.")
    return {"stats": stats, "decisions": decisions}


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Confirm SchoolScrapeUrl rows for the 20 selected districts."
    )
    parser.add_argument(
        "--json",
        type=Path,
        default=DEFAULT_JSON_PATH,
        help="Path to selected_schools_url_candidates_both.json.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print decisions without writing to the DB.",
    )
    args = parser.parse_args()

    try:
        asyncio.run(confirm_scrape_urls(args.json, args.dry_run))
    except Exception as exc:
        print(f"\nConfirm failed: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
