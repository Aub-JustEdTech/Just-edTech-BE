"""
Download- and ingest-time year filtering for school scraper media.

Calendar years in ``SCHOOL_SCRAPER_ALLOWED_YEARS`` gate crawl, persistence,
and download. After LLM classification, ``meeting_date.year`` is checked
again so documents with unknown URL years cannot reach the vector store
with out-of-range meeting dates.
"""

from __future__ import annotations

import logging
from datetime import date
from email.utils import parsedate_to_datetime

import httpx

from app.core.config import settings
from app.services.web_scraper._year_inference import infer_doc_year

logger = logging.getLogger(__name__)


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


async def fetch_url_last_modified_year(url: str) -> int | None:
    """Year from the file server's ``Last-Modified`` response header.

    A HEAD request only — no bytes of the file itself are transferred.
    Fallback for direct-hosted media (not YouTube — that has its own,
    more reliable, ``fetch_youtube_upload_year``) when the URL, filename,
    and page context carry no year.

    Not every server sends this header, and it reflects "when the file
    landed on the server," not necessarily the meeting date, so this is a
    last resort — called only once every free inference source has
    already failed.
    """
    try:
        async with httpx.AsyncClient(
            timeout=settings.WEB_SCRAPER_TIMEOUT_SECONDS, follow_redirects=True
        ) as client:
            response = await client.head(url)
        last_modified = response.headers.get("last-modified")
        if not last_modified:
            return None
        return parsedate_to_datetime(last_modified).year
    except Exception as exc:  # noqa: BLE001 — advisory only, never fatal
        logger.warning("Could not read Last-Modified header for %s: %s", url, exc)
        return None


async def evaluate_media_year_async(
    *,
    url: str,
    filename: str | None = None,
    source_page_url: str | None = None,
    parent_candidate_years: list[int] | None = None,
) -> tuple[int | None, bool, str | None]:
    """Like :func:`evaluate_media_year`, with a metadata-fetch fallback.

    URL/filename/page-context inference (``infer_doc_year``) never finds a
    year in a bare YouTube link, or a direct media link with no date
    anywhere in its path or surrounding text. Only for that case, and only
    once the cheap sources have already failed, this spends one metadata
    round-trip: YouTube's own upload date for YouTube URLs, or the file
    server's ``Last-Modified`` header for everything else.
    """
    inferred, should_process, skip_reason = evaluate_media_year(
        url=url,
        filename=filename,
        source_page_url=source_page_url,
        parent_candidate_years=parent_candidate_years,
    )
    if inferred is not None:
        return inferred, should_process, skip_reason

    from app.services.transcription.youtube import (
        fetch_youtube_upload_year,
        is_youtube_url,
    )

    if is_youtube_url(url):
        # When YouTube ingest is off, skip yt-dlp metadata fetches entirely.
        # Scrape persist and Celery ingest both call this; without the guard
        # SCHOOL_SCRAPER_YOUTUBE_TRANSCRIPT_ENABLED=false only blocks
        # transcription, not year-filter bot-check noise on EC2.
        if not settings.SCHOOL_SCRAPER_YOUTUBE_TRANSCRIPT_ENABLED:
            return inferred, should_process, skip_reason
        fallback_year = await fetch_youtube_upload_year(url)
    else:
        fallback_year = await fetch_url_last_modified_year(url)

    if fallback_year is None:
        return inferred, should_process, skip_reason

    allowed = allowed_calendar_years()
    if fallback_year in allowed:
        return fallback_year, True, None
    return fallback_year, False, f"year={fallback_year} not in {sorted(allowed)}"


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


async def filter_media_files_async(media_files: list[dict]) -> list[dict]:
    """Like :func:`filter_media_files`, with the metadata-fetch fallback.

    This is the filter ``scrape_media_files`` applies before a caller ever
    sees the media list, so it is the one that actually matters: a YouTube
    link or dateless direct-media link dropped here never reaches the
    per-item re-checks in ``run_scrape_districts.py`` / the ingest task,
    because by then it's already gone.
    """
    kept: list[dict] = []
    for media in media_files:
        _, should_process, _ = await evaluate_media_year_async(
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
