"""
Download- and ingest-time year filtering for school scraper media.

Calendar years in ``SCHOOL_SCRAPER_ALLOWED_YEARS`` gate crawl, persistence,
and download for DOCUMENTS. After LLM classification, ``meeting_date.year``
is checked again so documents with unknown URL years cannot reach the
vector store with out-of-range meeting dates.

Audio/video/youtube media uses a separate, day-precision date cutoff
(``SCHOOL_SCRAPER_MEDIA_CUTOFF_DATE``) instead of a year allow-list —
historical AV is out of scope entirely, so "which year" isn't precise
enough; see ``evaluate_media_cutoff`` / ``evaluate_media_cutoff_async``.
"""

from __future__ import annotations

import logging
from datetime import date, datetime
from email.utils import parsedate_to_datetime

import httpx

from app.core.config import settings
from app.services.web_scraper._year_inference import infer_doc_date, infer_doc_year
from app.services.web_scraper.url_keywords import is_meeting_related_media

logger = logging.getLogger(__name__)

# Media types the day-precision cutoff + keyword filter apply to. Documents
# keep the year-list behavior above, unchanged.
AV_MEDIA_TYPES = {"audio", "video", "youtube", "zoom"}


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


async def fetch_url_last_modified_date(url: str) -> date | None:
    """Date from the file server's ``Last-Modified`` response header.

    A HEAD request only — no bytes of the file itself are transferred.
    Fallback for direct-hosted media (not YouTube — that has its own,
    more reliable, ``fetch_youtube_upload_date``) when the URL, filename,
    and page context carry no date.

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
        parsed: datetime = parsedate_to_datetime(last_modified)
        return parsed.date()
    except Exception as exc:  # noqa: BLE001 — advisory only, never fatal
        logger.warning("Could not read Last-Modified header for %s: %s", url, exc)
        return None


async def fetch_url_last_modified_year(url: str) -> int | None:
    """Year from the file server's ``Last-Modified`` response header.

    Thin wrapper over :func:`fetch_url_last_modified_date` for callers that
    only need year-level precision (the document year-list filter).
    """
    last_modified_date = await fetch_url_last_modified_date(url)
    return last_modified_date.year if last_modified_date else None


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
        # When YouTube ingest is off, skip the metadata fetch entirely.
        # Scrape persist and Celery ingest both call this; without the guard
        # SCHOOL_SCRAPER_YOUTUBE_TRANSCRIPT_ENABLED=false only blocks
        # transcription, not this fallback call.
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


def evaluate_media_cutoff(
    *,
    url: str,
    filename: str | None = None,
    source_page_url: str | None = None,
) -> tuple[int | None, bool, str | None]:
    """Decide whether an audio/video/youtube item is on/after the AV cutoff.

    Unlike :func:`evaluate_media_year`, this compares an exact date
    (``SCHOOL_SCRAPER_MEDIA_CUTOFF_DATE``), not a year allow-list, since
    historical AV is out of scope entirely and "same year, wrong side of
    the cutoff" (e.g. March 2026 vs. the Sep 1 2026 cutoff) must resolve
    correctly, not just by year.

    Returns ``(inferred_year, should_process, skip_reason)`` — the same
    shape as :func:`evaluate_media_year` so callers don't need to branch on
    return type, even though the decision itself is date-based. When only a
    bare year is known and it equals the cutoff year, the decision is
    genuinely ambiguous (could be Jan or Dec) and is left unresolved here
    (``should_process=False`` with a distinct reason) for
    :func:`evaluate_media_cutoff_async` to resolve via a metadata fetch.
    """
    cutoff = settings.SCHOOL_SCRAPER_MEDIA_CUTOFF_DATE
    if cutoff is None:
        # Cutoff not configured — no AV date restriction.
        return None, True, None

    exact_date = infer_doc_date(
        url=url, filename=filename, source_page_url=source_page_url
    )
    if exact_date is not None:
        if exact_date >= cutoff:
            return exact_date.year, True, None
        return (
            exact_date.year,
            False,
            f"published {exact_date.isoformat()} is before the {cutoff.isoformat()} cutoff",
        )

    year = infer_doc_year(url=url, filename=filename, source_page_url=source_page_url)
    if year is not None:
        if year > cutoff.year:
            return year, True, None
        if year < cutoff.year:
            return year, False, f"year={year} is before the cutoff year {cutoff.year}"
        # year == cutoff.year: same year as the cutoff but no day precision
        # — genuinely can't tell yet. Caller (the async version) tries a
        # metadata fetch next; this sync version conservatively skips.
        return (
            year,
            False,
            f"year={year} matches the cutoff year but the exact date is unknown",
        )

    return None, False, "date could not be determined"


async def evaluate_media_cutoff_async(
    *,
    url: str,
    filename: str | None = None,
    source_page_url: str | None = None,
) -> tuple[int | None, bool, str | None]:
    """Like :func:`evaluate_media_cutoff`, with a metadata-fetch fallback.

    Only called when the cheap URL/filename/page-context check couldn't
    reach a decisive answer: no date/year found at all, or a bare year that
    ties the cutoff year exactly. Spends one metadata round-trip — YouTube's
    real upload date via the official Data API, or the file server's
    ``Last-Modified`` header for everything else — then makes the final
    call. If that also fails, the item is skipped with a clear
    "date could not be determined" reason rather than silently blocking the
    rest of the run.
    """
    cutoff = settings.SCHOOL_SCRAPER_MEDIA_CUTOFF_DATE
    if cutoff is None:
        return None, True, None

    year, should_process, skip_reason = evaluate_media_cutoff(
        url=url, filename=filename, source_page_url=source_page_url
    )
    if should_process:
        return year, True, None
    # A decisive skip is one where a real date/year was found and it lands
    # squarely outside the cutoff window — no metadata fetch can change
    # that answer, so don't spend one. Only the "unknown" and "ambiguous
    # same-year" cases are worth resolving further.
    if year is not None and year != cutoff.year:
        return year, should_process, skip_reason

    from app.services.transcription.youtube import (
        fetch_youtube_upload_date,
        is_youtube_url,
    )

    if is_youtube_url(url):
        if not settings.SCHOOL_SCRAPER_YOUTUBE_TRANSCRIPT_ENABLED:
            return year, should_process, skip_reason or "date could not be determined"
        fallback_date = await fetch_youtube_upload_date(url)
    else:
        fallback_date = await fetch_url_last_modified_date(url)

    if fallback_date is None:
        return year, should_process, skip_reason or "date could not be determined"

    if fallback_date >= cutoff:
        return fallback_date.year, True, None
    return (
        fallback_date.year,
        False,
        f"published {fallback_date.isoformat()} is before the {cutoff.isoformat()} cutoff",
    )


async def evaluate_media_processability_async(
    *,
    media_type: str,
    url: str,
    filename: str | None = None,
    source_page_url: str | None = None,
    parent_candidate_years: list[int] | None = None,
) -> tuple[int | None, bool, str | None]:
    """Route to the right date check based on ``media_type``.

    Audio/video/youtube use the day-precision cutoff
    (:func:`evaluate_media_cutoff_async`); everything else (documents) keeps
    the existing year-list check (:func:`evaluate_media_year_async`)
    unchanged. Single entry point so every call site (scrape-time filter,
    ingest-time recheck, admin backfill) applies the same rule consistently.
    """
    if media_type in AV_MEDIA_TYPES:
        return await evaluate_media_cutoff_async(
            url=url, filename=filename, source_page_url=source_page_url
        )
    return await evaluate_media_year_async(
        url=url,
        filename=filename,
        source_page_url=source_page_url,
        parent_candidate_years=parent_candidate_years,
    )


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
    """Drop media dicts whose inferred year/date is outside the allowed set.

    Audio/video/youtube additionally must match the meeting keyword list
    (see ``url_keywords.is_meeting_related_media``) — documents are not
    keyword-filtered.
    """
    from app.services.transcription.youtube import is_youtube_url

    kept: list[dict] = []
    for media in media_files:
        if is_youtube_url(media["url"]) and not settings.SCHOOL_SCRAPER_YOUTUBE_TRANSCRIPT_ENABLED:
            continue

        media_type = media.get("media_type")
        if media_type in AV_MEDIA_TYPES:
            if not is_meeting_related_media(
                page_url=media.get("source_page_url"),
                media_url=media.get("url"),
                title=media.get("name"),
            ):
                continue
            _, should_process, _ = evaluate_media_cutoff(
                url=media["url"],
                filename=media.get("name"),
                source_page_url=media.get("source_page_url"),
            )
            if should_process:
                kept.append(media)
            continue

        # Documents: unchanged year-list logic.
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

    Audio/video/youtube additionally must match the meeting keyword list —
    checked against the page it was found on, its own title/filename, and
    its direct media URL (any one match is enough). Documents are not
    keyword-filtered; this only applies to the AV cutoff path.
    """
    from app.services.transcription.youtube import is_youtube_url

    kept: list[dict] = []
    for media in media_files:
        if is_youtube_url(media["url"]) and not settings.SCHOOL_SCRAPER_YOUTUBE_TRANSCRIPT_ENABLED:
            continue

        media_type = media.get("media_type")
        if media_type in AV_MEDIA_TYPES:
            if not is_meeting_related_media(
                page_url=media.get("source_page_url"),
                media_url=media.get("url"),
                title=media.get("name"),
            ):
                continue
            _, should_process, _ = await evaluate_media_cutoff_async(
                url=media["url"],
                filename=media.get("name"),
                source_page_url=media.get("source_page_url"),
            )
        else:
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
