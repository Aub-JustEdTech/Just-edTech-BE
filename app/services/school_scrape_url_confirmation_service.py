"""
Load offline URL-discovery candidates and merge with school confirm state.

Candidates are read from a JSON file (see SCHOOL_URL_CANDIDATES_JSON_PATH).
Schools are matched by org_code within the current tenant.

Manual candidates (typed in the FE) are stored as active ``school_scrape_urls``
rows and merged into the review list alongside discovered URLs.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Literal

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.crud import schools as schools_crud
from app.models.school import School
from app.schemas.schools import (
    ScrapeUrlCandidateOut,
    SchoolCandidateReviewOut,
)
from app.utils.school_url_candidates import (
    candidate_dedupe_key,
    dedupe_and_rank_candidates,
)

logger = logging.getLogger(__name__)

ConfirmationStatus = Literal["added", "not_added"] | None

_json_cache: tuple[float, list[dict]] | None = None


def _load_json_records() -> list[dict]:
    """Load and cache discovery records from the configured JSON file."""
    global _json_cache  # noqa: PLW0603

    path = Path(settings.SCHOOL_URL_CANDIDATES_JSON_PATH)
    if not path.is_file():
        raise FileNotFoundError(
            f"URL candidates JSON not found: {path}. "
            "Set SCHOOL_URL_CANDIDATES_JSON_PATH or add the file."
        )

    mtime = path.stat().st_mtime
    if _json_cache is not None and _json_cache[0] == mtime:
        return _json_cache[1]

    with path.open("r", encoding="utf-8") as handle:
        records = json.load(handle)
    if not isinstance(records, list):
        raise ValueError(f"Expected a JSON array in {path}")

    _json_cache = (mtime, records)
    logger.debug("Loaded %s URL candidate records from %s", len(records), path)
    return records


def _confirmed_url_for_school(school: School | None) -> tuple[bool, str | None, int | None]:
    if school is None or school.scrape_url_id is None:
        return False, None, None
    for scrape_url in school.scrape_urls or []:
        if scrape_url.id == school.scrape_url_id and scrape_url.is_active:
            return True, scrape_url.url, scrape_url.id
    return False, None, school.scrape_url_id


def _scrape_url_id_by_key(school: School | None) -> dict[str, int]:
    """Map candidate dedupe keys → active scrape_url ids for a school."""
    if school is None:
        return {}
    mapping: dict[str, int] = {}
    for scrape_url in school.scrape_urls or []:
        if not scrape_url.is_active:
            continue
        mapping[candidate_dedupe_key(scrape_url.url)] = scrape_url.id
    return mapping


def _format_candidates(
    raw_candidates: list[dict],
    *,
    max_candidates: int,
    school: School | None = None,
) -> list[ScrapeUrlCandidateOut]:
    """Rank discovered candidates and merge active manual scrape URLs."""
    has_confirmed, confirmed_url, confirmed_id = _confirmed_url_for_school(school)
    confirmed_key = candidate_dedupe_key(confirmed_url) if confirmed_url else None
    id_by_key = _scrape_url_id_by_key(school)

    ranked = dedupe_and_rank_candidates(
        raw_candidates,
        max_candidates=max_candidates,
        preserve_query=True,
    )

    seen_keys: set[str] = set()
    out: list[ScrapeUrlCandidateOut] = []

    for index, row in enumerate(ranked):
        key = candidate_dedupe_key(row["url"])
        seen_keys.add(key)
        out.append(
            ScrapeUrlCandidateOut(
                rank=index + 1,
                url=row["url"],
                score=row["score"],
                matched_keywords=list(row.get("matched_keywords") or []),
                data_type=row.get("data_type"),
                is_archive=bool(row.get("is_archive") or False),
                data_years_available=list(row.get("data_years_available") or []),
                source="discovered",
                is_selected=bool(confirmed_key and key == confirmed_key),
                scrape_url_id=id_by_key.get(key)
                or (confirmed_id if confirmed_key and key == confirmed_key else None),
            )
        )

    # Append active DB URLs that are not already in the discovered list
    # (manual adds, or a confirmed URL that wasn't in the JSON).
    if school is not None:
        for scrape_url in school.scrape_urls or []:
            if not scrape_url.is_active:
                continue
            key = candidate_dedupe_key(scrape_url.url)
            if key in seen_keys:
                continue
            seen_keys.add(key)
            is_selected = has_confirmed and scrape_url.id == confirmed_id
            out.append(
                ScrapeUrlCandidateOut(
                    rank=len(out) + 1,
                    url=scrape_url.url,
                    score=0,
                    matched_keywords=[],
                    data_type=None,
                    is_archive=False,
                    data_years_available=[],
                    source="manual",
                    is_selected=is_selected,
                    scrape_url_id=scrape_url.id,
                )
            )

    # Keep selected candidate visible near the top for FE edit UX.
    if any(c.is_selected for c in out):
        selected = [c for c in out if c.is_selected]
        others = [c for c in out if not c.is_selected]
        out = selected + others
        for index, candidate in enumerate(out):
            candidate.rank = index + 1

    return out[:max_candidates]


def _build_review_row(
    record: dict,
    school: School | None,
    *,
    max_candidates: int,
) -> SchoolCandidateReviewOut:
    has_confirmed, confirmed_url, confirmed_id = _confirmed_url_for_school(school)
    candidates = _format_candidates(
        record.get("candidates") or [],
        max_candidates=max_candidates,
        school=school,
    )
    return SchoolCandidateReviewOut(
        school_id=school.id if school else None,
        org_code=str(record.get("org_code") or ""),
        name=str(record.get("name") or ""),
        website=record.get("website") or (school.website if school else None),
        in_database=school is not None,
        has_confirmed_scrape_url=has_confirmed,
        confirmed_scrape_url=confirmed_url,
        confirmed_scrape_url_id=confirmed_id,
        discovery_method=record.get("discovery_method"),
        total_urls_scanned=int(record.get("total_urls_scanned") or 0),
        total_candidates=len(candidates),
        candidates=candidates,
    )


async def _schools_by_org_code(
    db: AsyncSession, tenant_id: int, org_codes: list[str]
) -> dict[str, School]:
    return await schools_crud.list_schools_by_org_codes(db, tenant_id, org_codes)


async def list_candidate_reviews(
    db: AsyncSession,
    tenant_id: int,
    *,
    confirmation_status: ConfirmationStatus = None,
    skip: int = 0,
    limit: int = 50,
    max_candidates: int | None = None,
) -> tuple[list[SchoolCandidateReviewOut], int, int, int]:
    """Return paginated candidate reviews and overall added/not-added counts."""
    max_candidates = max_candidates or settings.SCHOOL_SCRAPER_MAX_CANDIDATES
    records = _load_json_records()
    org_codes = [str(r.get("org_code") or "") for r in records]
    schools_map = await _schools_by_org_code(db, tenant_id, org_codes)

    all_rows = [
        _build_review_row(
            record,
            schools_map.get(str(record.get("org_code") or "")),
            max_candidates=max_candidates,
        )
        for record in records
    ]

    added_count = sum(1 for row in all_rows if row.has_confirmed_scrape_url)
    not_added_count = len(all_rows) - added_count

    if confirmation_status == "added":
        filtered = [row for row in all_rows if row.has_confirmed_scrape_url]
    elif confirmation_status == "not_added":
        filtered = [row for row in all_rows if not row.has_confirmed_scrape_url]
    else:
        filtered = all_rows

    total = len(filtered)
    page = filtered[skip : skip + limit]
    return page, total, added_count, not_added_count


async def get_candidate_review(
    db: AsyncSession,
    tenant_id: int,
    school_id: int,
    *,
    max_candidates: int | None = None,
) -> SchoolCandidateReviewOut | None:
    max_candidates = max_candidates or settings.SCHOOL_SCRAPER_MAX_CANDIDATES
    school = await schools_crud.get_school(db, tenant_id, school_id)
    if school is None:
        return None

    for record in _load_json_records():
        if str(record.get("org_code") or "") == school.org_code:
            return _build_review_row(record, school, max_candidates=max_candidates)
    return None
