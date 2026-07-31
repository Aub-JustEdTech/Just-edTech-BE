"""
Generalised school website scraper endpoints.

Two-step workflow:
  POST /school-scraper/discover    — finds candidate meeting-archive URLs
  POST /school-scraper/scrape-media — scrapes audio, video and document files from a confirmed URL
"""

import logging

import httpx
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.school_scraper import (
    BackfillYearsRequest,
    BackfillYearsResponse,
    CandidateUrl,
    DiscoverRequest,
    DiscoverResponse,
    MediaFileResult,
    MediaTypeSummary,
    ScrapeMediaRequest,
    ScrapeMediaResponse,
)
from app.schemas.users import User
from app.services.web_scraper.discovery_dispatch import discover_with_ranking_mode
from app.services.web_scraper.school_scraper_service import SchoolScraperService
from app.utils.dependencies import (
    get_current_tenant_admin,
    get_current_tenant_user,
    get_db,
)

router = APIRouter()
logger = logging.getLogger(__name__)


@router.post(
    "/discover",
    response_model=DiscoverResponse,
    status_code=status.HTTP_200_OK,
    summary="Discover candidate meeting-archive URLs from a school website",
)
async def discover_urls(
    request: DiscoverRequest,
    current_user: User = Depends(get_current_tenant_user),
) -> DiscoverResponse:
    """
    Step 1 — URL Discovery.

    Given a school website's base URL, this endpoint tries to find URLs that
    are likely to contain meeting minutes or board archives.

    **Discovery order:**
    1. `/wp-sitemap.xml` (WordPress sitemap index + child sitemaps)
    2. `/sitemap.xml` (generic sitemap or sitemap index)
    3. `robots.txt` (`Sitemap:` directive)
    4. Homepage navigation crawl (fallback)

    All collected URLs are then filtered by keywords such as *meeting*,
    *minutes*, *board*, *archives*, *agenda*, etc.

    **Response:** a ranked list of up to `max_candidates` candidate URLs.
    The frontend shows these to the user who picks one and calls `/scrape-media`.

    **Example:**
    ```bash
    curl -X POST "http://localhost:8000/api/v1/school-scraper/discover" \\
      -H "Authorization: Bearer YOUR_TOKEN" \\
      -H "Content-Type: application/json" \\
      -d '{"base_url": "https://www.akfcs.org"}'
    ```
    """
    try:
        result = await discover_with_ranking_mode(
            base_url=request.base_url,
            max_candidates=request.max_candidates,
            use_playwright=request.use_playwright,
        )
    except httpx.TimeoutException:
        raise HTTPException(
            status_code=status.HTTP_408_REQUEST_TIMEOUT,
            detail=f"Request timed out while accessing {request.base_url}",
        )
    except httpx.NetworkError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Network error while accessing {request.base_url}: {exc}",
        )
    except Exception as exc:
        logger.exception("Unexpected error during URL discovery for %s", request.base_url)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Unexpected error: {exc}",
        )

    candidates = [
        CandidateUrl(
            url=c["url"],
            matched_keywords=c.get("matched_keywords", []),
            score=c.get("score", 0),
            data_type=c.get("data_type"),
            is_archive=c.get("is_archive", False),
            data_years_available=c.get("data_years_available", []),
        )
        for c in result["candidates"]
    ]

    return DiscoverResponse(
        base_url=result["base_url"],
        discovery_method=result["discovery_method"],
        total_urls_scanned=result["total_urls_scanned"],
        total_candidates=len(candidates),
        candidates=candidates,
        ranking_mode=result.get("ranking_mode", "keyword"),
    )


@router.post(
    "/scrape-media",
    response_model=ScrapeMediaResponse,
    status_code=status.HTTP_200_OK,
    summary="Scrape audio, video and document files from a school meeting-archive page",
)
async def scrape_media(
    request: ScrapeMediaRequest,
    current_user: User = Depends(get_current_tenant_user),
) -> ScrapeMediaResponse:
    """
    Step 2 — Media Scraping.

    Given the confirmed meeting-archive URL (chosen by the user from `/discover`
    results), this endpoint scrapes the page and returns all audio, video and
    document file links found on it (e.g. PDF/Word minutes and agendas, in
    addition to recorded meeting audio/video).

    It also follows same-domain sub-page links (e.g. year-archive pages like
    `/meeting-archives/2024/`) up to `crawl_depth` levels deep (default 1,
    max 3).

    **Supported media types (current):**
    - Video: `.mp4`, `.mov`, `.webm`
    - Audio: `.mp3`, `.wav`, `.m4a`
    - Document: `.pdf`, `.docx`, `.doc`, `.xlsx`, `.xls`, `.pptx`, `.ppt`

    The returned file list can be displayed in the frontend. The user selects
    specific files which are then passed to the extraction pipeline.

    **Example:**
    ```bash
    curl -X POST "http://localhost:8000/api/v1/school-scraper/scrape-media" \\
      -H "Authorization: Bearer YOUR_TOKEN" \\
      -H "Content-Type: application/json" \\
      -d '{"url": "https://www.akfcs.org/meeting-archives/", "crawl_depth": 1}'
    ```
    """
    async with SchoolScraperService() as scraper:
        try:
            result = await scraper.scrape_media_files(
                page_url=request.url,
                crawl_depth=request.crawl_depth,
            )
        except httpx.TimeoutException:
            raise HTTPException(
                status_code=status.HTTP_408_REQUEST_TIMEOUT,
                detail=f"Request timed out while accessing {request.url}",
            )
        except httpx.HTTPStatusError as exc:
            http_status = exc.response.status_code if exc.response else "unknown"
            if http_status == 404:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Page not found (404): {request.url}",
                )
            elif http_status == 403:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail=f"Access forbidden (403): {request.url}",
                )
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"HTTP error {http_status} fetching {request.url}",
            )
        except httpx.NetworkError as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Network error while accessing {request.url}: {exc}",
            )
        except Exception as exc:
            logger.exception("Unexpected error during media scrape for %s", request.url)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Unexpected error: {exc}",
            )

    media_files = [
        MediaFileResult(
            name=m["name"],
            url=m["url"],
            file_extension=m["file_extension"],
            media_type=m["media_type"],
            size_bytes=m["size_bytes"],
            source_page_url=m["source_page_url"],
        )
        for m in result["media_files"]
    ]

    type_counts: dict[str, int] = {"video": 0, "audio": 0, "document": 0}
    for m in media_files:
        type_counts[m.media_type] = type_counts.get(m.media_type, 0) + 1

    return ScrapeMediaResponse(
        source_url=result["source_url"],
        pages_crawled=result["pages_crawled"],
        total_media_found=len(media_files),
        media_type_summary=MediaTypeSummary(**type_counts),
        media_files=media_files,
    )


@router.post(
    "/backfill-years",
    response_model=BackfillYearsResponse,
    status_code=status.HTTP_200_OK,
    summary="Re-evaluate skipped_year scraped media against the current allowed-years set (admin)",
)
async def backfill_years(
    payload: BackfillYearsRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_tenant_admin),
) -> BackfillYearsResponse:
    """Re-queue scraped_media rows previously skipped by the year filter.

    After widening `SCHOOL_SCRAPER_ALLOWED_YEARS` (e.g. adding 2023),
    call this endpoint to re-evaluate every `status='skipped_year'` row
    without re-crawling the source site. Each row's `doc_year` is
    re-inferred from its URL/filename/page context; rows whose inferred
    year is now in the allowed set (or whose year could not be inferred
    and `SCHOOL_SCRAPER_DOWNLOAD_ON_UNKNOWN_YEAR=True`) are flipped back
    to `status='discovered'` and re-enqueued for ingestion.

    Optionally scope to a single school via `school_id`.
    """
    from app.crud.schools import list_skipped_year_media, update_scraped_media
    from app.services.web_scraper.year_filter import evaluate_media_year
    from app.tasks.school_scraper_tasks import ingest_scraped_media

    rows = await list_skipped_year_media(
        db,
        tenant_id=current_user.tenant_id,
        school_id=payload.school_id,
    )

    enqueued = 0
    skipped = 0
    for sm in rows:
        inferred, in_range, _reason = evaluate_media_year(
            url=sm.source_media_url,
            filename=sm.original_name,
            source_page_url=sm.source_page_url,
        )
        if inferred is not None:
            sm.doc_year = inferred
        if not in_range:
            skipped += 1
            continue
        await update_scraped_media(db, sm.id, status="discovered", doc_year=inferred)
        ingest_scraped_media.delay(scraped_media_id=sm.id)
        enqueued += 1

    return BackfillYearsResponse(
        enqueued=enqueued,
        skipped=skipped,
        message=(
            f"Re-evaluated {len(rows)} skipped_year rows: "
            f"{enqueued} re-queued, {skipped} still out of range."
        ),
    )
