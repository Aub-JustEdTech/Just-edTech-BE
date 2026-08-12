"""
Download- and ingest-time year filtering for school scraper media.

Calendar years in ``SCHOOL_SCRAPER_ALLOWED_YEARS`` gate crawl, persistence,
and download. After LLM classification, ``meeting_date.year`` is checked
again so documents with unknown URL years cannot reach the vector store
with out-of-range meeting dates.
"""

from __future__ import annotations

from datetime import date

from app.core.config import settings
from app.services.web_scraper._year_inference import infer_doc_year


def allowed_calendar_years() -> set[int]:
    """Return the configured set of allowed 4-digit calendar years."""
    return set(settings.SCHOOL_SCRAPER_ALLOWED_YEARS)


def evaluate_media_year(
    *,
    url: str,
    filename: str | None = None,
    source_page_url: str | None = None,
    parent_candidate_years: list[int] | None = None,
) -> tuple[int | None, bool, str | None]:
    """Decide whether a discovered media URL should be downloaded/ingested.

    Returns ``(inferred_year, should_process, skip_reason)``.
    """
    inferred = infer_doc_year(
        url=url,
        filename=filename,
        source_page_url=source_page_url,
        parent_candidate_years=parent_candidate_years,
    )
    allowed = allowed_calendar_years()

    if inferred is not None:
        if inferred in allowed:
            return inferred, True, None
        return (
            inferred,
            False,
            f"year={inferred} not in {sorted(allowed)}",
        )

    if settings.SCHOOL_SCRAPER_DOWNLOAD_ON_UNKNOWN_YEAR:
        return None, True, None

    return None, False, "year could not be inferred"


def should_crawl_page_url(url: str) -> bool:
    """Return False when a sub-page URL clearly targets an out-of-range year.

    Pages with no inferrable year (mixed archive index pages) are still
    crawled so individual document links can be filtered separately.
    """
    inferred = infer_doc_year(url=url, filename=None, source_page_url=None)
    if inferred is None:
        return True
    return inferred in allowed_calendar_years()


def filter_media_files(media_files: list[dict]) -> list[dict]:
    """Drop media dicts whose inferred year is outside the allowed set."""
    kept: list[dict] = []
    for media in media_files:
        # If doc_year is already set (e.g., from board platform expanders that
        # extracted year from meeting dates), use it directly instead of re-inferring
        existing_year = media.get("doc_year")
        if existing_year is not None:
            # Already has a year - just check if it's in the allowed range
            allowed = allowed_calendar_years()
            should_process = existing_year in allowed
        else:
            # No year set yet - infer from URL/filename
            _, should_process, _ = evaluate_media_year(
                url=media["url"],
                filename=media.get("name"),
                source_page_url=media.get("source_page_url"),
            )
        
        if should_process:
            kept.append(media)
    return kept


def is_meeting_date_in_range(meeting_date: date | None) -> bool:
    """Return True when ``meeting_date`` falls in an allowed calendar year."""
    if meeting_date is None:
        return False
    return meeting_date.year in allowed_calendar_years()
