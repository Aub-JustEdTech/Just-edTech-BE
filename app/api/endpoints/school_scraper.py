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

from app.crud import schools as crud
from app.models.school import SchoolScrapeUrl
from app.schemas.school_scraper import (
    BackfillYearsRequest,
    BackfillYearsResponse,
    CandidateUrl,
    DiscoverRequest,
    DiscoverResponse,
    MediaFileResult,
    MediaTypeSummary,
    ScrapeMediaBatchRequest,
    ScrapeMediaBatchResponse,
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
    get_effective_tenant_id,
)

router = APIRouter()
logger = logging.getLogger(__name__)


async def _resolve_scrape_url(
    db: AsyncSession, tenant_id: int, school_id: int, scrape_url_id: int
) -> SchoolScrapeUrl:
    school = await crud.get_school(db, tenant_id, school_id)
    if not school:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"School {school_id} not found in tenant {tenant_id}",
        )
    scrape_url = next(
        (u for u in school.scrape_urls if u.id == scrape_url_id), None
    )
    if not scrape_url:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Scrape URL {scrape_url_id} not found for school {school_id}",
        )
    return scrape_url


async def _scrape_and_maybe_persist(
    db: AsyncSession,
    *,
    url: str,
    crawl_depth: int,
    scrape_url: SchoolScrapeUrl | None,
) -> ScrapeMediaResponse:
    """Run one scrape attempt.

    When `scrape_url` is given, the outcome (success or failure) is written
    onto that row and a structured result is always returned — never raised
    — so a bad target page shows up as `Invalid` in the FE instead of the
    whole request erroring out. Without `scrape_url` (stateless/preview use,
    e.g. discovery review), behavior is unchanged: failures raise HTTPException.
    """
    async with SchoolScraperService() as scraper:
        try:
            result = await scraper.scrape_media_files(
                page_url=url, crawl_depth=crawl_depth
            )
        except httpx.TimeoutException:
            if scrape_url is not None:
                await crud.record_scrape_result(
                    db, scrape_url, http_status=None, page_count=None
                )
                return ScrapeMediaResponse(
                    source_url=url,
                    scrape_url_id=scrape_url.id,
                    success=False,
                    http_status=None,
                    pages_crawled=0,
                    total_media_found=0,
                    media_type_summary=MediaTypeSummary(),
                    media_files=[],
                )
            raise HTTPException(
                status_code=status.HTTP_408_REQUEST_TIMEOUT,
                detail=f"Request timed out while accessing {url}",
            ) from None
        except httpx.HTTPStatusError as exc:
            http_status = exc.response.status_code if exc.response else None
            if scrape_url is not None:
                await crud.record_scrape_result(
                    db, scrape_url, http_status=http_status, page_count=None
                )
                return ScrapeMediaResponse(
                    source_url=url,
                    scrape_url_id=scrape_url.id,
                    success=False,
                    http_status=http_status,
                    pages_crawled=0,
                    total_media_found=0,
                    media_type_summary=MediaTypeSummary(),
                    media_files=[],
                )
            if http_status == 404:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Page not found (404): {url}",
                ) from exc
            elif http_status == 403:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail=f"Access forbidden (403): {url}",
                ) from exc
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"HTTP error {http_status} fetching {url}",
            ) from exc
        except httpx.NetworkError as exc:
            if scrape_url is not None:
                await crud.record_scrape_result(
                    db, scrape_url, http_status=None, page_count=None
                )
                return ScrapeMediaResponse(
                    source_url=url,
                    scrape_url_id=scrape_url.id,
                    success=False,
                    http_status=None,
                    pages_crawled=0,
                    total_media_found=0,
                    media_type_summary=MediaTypeSummary(),
                    media_files=[],
                )
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Network error while accessing {url}: {exc}",
            ) from exc
        except Exception as exc:
            logger.exception("Unexpected error during media scrape for %s", url)
            if scrape_url is not None:
                await crud.record_scrape_result(
                    db, scrape_url, http_status=None, page_count=None
                )
                return ScrapeMediaResponse(
                    source_url=url,
                    scrape_url_id=scrape_url.id,
                    success=False,
                    http_status=None,
                    pages_crawled=0,
                    total_media_found=0,
                    media_type_summary=MediaTypeSummary(),
                    media_files=[],
                )
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Unexpected error: {exc}",
            ) from exc

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

    if scrape_url is not None:
        await crud.record_scrape_result(
            db, scrape_url, http_status=200, page_count=result["pages_crawled"]
        )

    return ScrapeMediaResponse(
        source_url=result["source_url"],
        scrape_url_id=scrape_url.id if scrape_url is not None else None,
        success=True,
        http_status=200,
        pages_crawled=result["pages_crawled"],
        total_media_found=len(media_files),
        media_type_summary=MediaTypeSummary(**type_counts),
        media_files=media_files,
    )


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
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_tenant_user),
    tenant_id: int = Depends(get_effective_tenant_id),
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
    scrape_url = None
    if request.school_id is not None and request.scrape_url_id is not None:
        scrape_url = await _resolve_scrape_url(
            db, tenant_id, request.school_id, request.scrape_url_id
        )

    response = await _scrape_and_maybe_persist(
        db,
        url=request.url,
        crawl_depth=request.crawl_depth,
        scrape_url=scrape_url,
    )

    if request.persist and response.success:
        if request.school_id is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="school_id is required when persist=True",
            )

        from app.crud.schools import bulk_create_scraped_media, get_school

        # Tenant-scoped: without this any tenant could attach media to any
        # school by guessing an id.
        school = await get_school(db, tenant_id, request.school_id)
        if not school:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"School {request.school_id} not found for this tenant",
            )

        rows, skipped_duplicates = await bulk_create_scraped_media(
            db,
            school=school,
            source_page_url=request.url,
            media_files=[m.model_dump() for m in response.media_files],
        )
        response.persisted = len(rows)
        response.skipped_duplicates = skipped_duplicates
        response.scraped_media_ids = [r.id for r in rows]

        # Imported here, not at module scope, to avoid the tasks -> models
        # import cycle (matches backfill_years below).
        from app.tasks.school_scraper_tasks import ingest_scraped_media

        # Enqueued only AFTER the commit above — otherwise the worker does
        # db.get(ScrapedMedia, id) -> None and silently drops the item.
        for row in rows:
            ingest_scraped_media.delay(row.id)
            response.enqueued += 1

    return response


@router.post(
    "/scrape-media-batch",
    response_model=ScrapeMediaBatchResponse,
    status_code=status.HTTP_200_OK,
    summary="Scrape multiple confirmed source URLs for one school in one call",
)
async def scrape_media_batch(
    request: ScrapeMediaBatchRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_tenant_user),
    tenant_id: int = Depends(get_effective_tenant_id),
) -> ScrapeMediaBatchResponse:
    """Scrape every listed SchoolScrapeUrl id for a school and persist each
    result independently, so one failing URL doesn't block the others —
    backs the "Scrape selected" multi-select action on the Schools admin page.
    """
    school = await crud.get_school(db, tenant_id, request.school_id)
    if not school:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"School {request.school_id} not found in tenant {tenant_id}",
        )
    urls_by_id = {u.id: u for u in school.scrape_urls}

    results: list[ScrapeMediaResponse] = []
    for scrape_url_id in request.scrape_url_ids:
        scrape_url = urls_by_id.get(scrape_url_id)
        if not scrape_url:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Scrape URL {scrape_url_id} not found for school {request.school_id}",
            )
        results.append(
            await _scrape_and_maybe_persist(
                db,
                url=scrape_url.url,
                crawl_depth=request.crawl_depth,
                scrape_url=scrape_url,
            )
        )

    return ScrapeMediaBatchResponse(results=results)


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
    from app.services.web_scraper.year_filter import evaluate_media_year_async
    from app.tasks.school_scraper_tasks import ingest_scraped_media

    rows = await list_skipped_year_media(
        db,
        tenant_id=current_user.tenant_id,
        school_id=payload.school_id,
    )

    enqueued = 0
    skipped = 0
    for sm in rows:
        inferred, in_range, _reason = await evaluate_media_year_async(
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
