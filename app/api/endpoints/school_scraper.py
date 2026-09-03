"""
Generalised school website scraper endpoints.

Two-step workflow:
  POST /school-scraper/discover    — finds candidate meeting-archive URLs
  POST /school-scraper/scrape-media — scrapes audio, video and document files from a confirmed URL
"""

import logging
from datetime import timedelta

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
    ScrapeAllResponse,
    ScrapeMediaBatchRequest,
    ScrapeMediaBatchStatusResponse,
    ScrapeMediaBatchTaskResponse,
    ScrapeMediaRequest,
    ScrapeMediaResponse,
    ScrapeStatusResponse,
    TranscriptPreviewRequest,
    TranscriptPreviewResponse,
)
from app.schemas.users import User
from app.services.transcript_preview_service import transcript_preview_service
from app.services.web_scraper.discovery_dispatch import discover_with_ranking_mode
from app.services.web_scraper.school_scraper_service import SchoolScraperService
from app.utils.dependencies import (
    get_current_tenant_admin,
    get_current_tenant_user,
    get_db,
    get_effective_tenant_id,
)
from app.utils.response import success_response

router = APIRouter()
logger = logging.getLogger(__name__)

# One tenant-wide sweep at a time. Value is the Celery task_id; the status
# endpoint lazily clears the key once that task is ready, so no task
# callback wiring is needed. TTL is a safety net only, in case a worker
# dies without ever completing (or failing) the task.
_SCRAPE_RUNNING_KEY_PREFIX = "scrape_running:"
_SCRAPE_RUNNING_TTL = timedelta(hours=6)


def _scrape_running_key(tenant_id: int) -> str:
    return f"{_SCRAPE_RUNNING_KEY_PREFIX}{tenant_id}"


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


def _media_type_summary(media_files: list[dict]) -> MediaTypeSummary:
    type_counts: dict[str, int] = {"video": 0, "audio": 0, "document": 0}
    for m in media_files:
        media_type = m["media_type"]
        type_counts[media_type] = type_counts.get(media_type, 0) + 1
    return MediaTypeSummary(**type_counts)


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
        max_pages_limit_reached=result.get("max_pages_limit_reached", False),
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
):
    """
    Step 2 — Media Scraping.

    Given the confirmed meeting-archive URL (chosen by the user from `/discover`
    results), this endpoint scrapes the page and returns all audio, video and
    document file links found on it (e.g. PDF/Word minutes and agendas, in
    addition to recorded meeting audio/video).

    It also follows same-domain sub-page links (e.g. year-archive pages like
    `/meeting-archives/2024/`) up to `crawl_depth` levels deep (default 4,
    max 4).

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
      -d '{"url": "https://www.akfcs.org/meeting-archives/", "crawl_depth": 4}'
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

    return success_response(data=response)


@router.post(
    "/scrape-media-batch",
    response_model=ScrapeMediaBatchTaskResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Kick off a background scrape of multiple confirmed source URLs for one school",
)
async def scrape_media_batch(
    request: ScrapeMediaBatchRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_tenant_user),
    tenant_id: int = Depends(get_effective_tenant_id),
) -> ScrapeMediaBatchTaskResponse:
    """Validates the school + URLs synchronously (fast, DB-only — a bad id
    still 404s immediately), then hands the slow part to a Celery task on
    the `scraping` queue, which crawls every URL concurrently instead of
    the old sequential loop. Poll `/scrape-media-batch/status?task_id=...`
    for the result.

    Always persists: each URL's discovered media is saved to scraped_media
    and newly created rows are enqueued for ingestion. Unlike /scrape-media,
    there is no preview-only mode here — "Scrape selected" has exactly one
    caller and it always wants the result saved.
    """
    school = await crud.get_school(db, tenant_id, request.school_id)
    if not school:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"School {request.school_id} not found in tenant {tenant_id}",
        )
    urls_by_id = {u.id: u for u in school.scrape_urls}
    missing = [i for i in request.scrape_url_ids if i not in urls_by_id]
    if missing:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Scrape URL(s) {missing} not found for school {request.school_id}",
        )

    # Imported here, not at module scope, to avoid the tasks -> models
    # import cycle (matches backfill_years/scrape_media above).
    from app.tasks.school_scraper_tasks import scrape_media_batch as scrape_media_batch_task

    task = scrape_media_batch_task.delay(
        tenant_id,
        request.school_id,
        request.scrape_url_ids,
        request.crawl_depth,
    )
    return ScrapeMediaBatchTaskResponse(task_id=task.id, status="PENDING")


@router.get(
    "/scrape-media-batch/status",
    response_model=ScrapeMediaBatchStatusResponse,
    status_code=status.HTTP_200_OK,
    summary="Poll the status/result of a batch scrape kicked off by POST /scrape-media-batch",
)
async def get_scrape_media_batch_status(
    task_id: str,
    current_user: User = Depends(get_current_tenant_user),
) -> ScrapeMediaBatchStatusResponse:
    from app.celery_app import celery_app

    task_result = celery_app.AsyncResult(task_id)
    if not task_result.ready():
        return ScrapeMediaBatchStatusResponse(
            running=True, task_status=task_result.state
        )

    raw = task_result.result
    if not isinstance(raw, dict):
        # A worker crash/timeout surfaces here as a non-dict result
        # (typically an exception instance) — report it rather than 500.
        return ScrapeMediaBatchStatusResponse(
            running=False, task_status=task_result.state, error=str(raw)
        )
    if raw.get("error"):
        return ScrapeMediaBatchStatusResponse(
            running=False, task_status=task_result.state, error=raw["error"]
        )

    results = [
        ScrapeMediaResponse(
            source_url=r["source_url"],
            scrape_url_id=r["scrape_url_id"],
            success=r["success"],
            http_status=r["http_status"],
            pages_crawled=r["pages_crawled"],
            total_media_found=len(r["media_files"]),
            media_type_summary=_media_type_summary(r["media_files"]),
            media_files=[MediaFileResult(**m) for m in r["media_files"]],
            persisted=r["persisted"],
            skipped_duplicates=r["skipped_duplicates"],
            enqueued=r["enqueued"],
            scraped_media_ids=r["scraped_media_ids"],
        )
        for r in raw["results"]
    ]
    return ScrapeMediaBatchStatusResponse(
        running=False, task_status=task_result.state, results=results
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


@router.post(
    "/scrape-all",
    response_model=ScrapeAllResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Scrape every active source URL for the tenant (background job)",
)
async def scrape_all_sources(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_tenant_user),
    tenant_id: int = Depends(get_effective_tenant_id),
) -> ScrapeAllResponse:
    """Tenant-wide "Scrape all sources" action for the Schools page header —
    queues the same sweep task used for the nightly scheduled run, across
    every school in this tenant. Only one sweep may run per tenant at a
    time, so the common button and every per-school scrape button in the
    UI can share one disabled state.
    """
    from app.celery_app import celery_app
    from app.db.redis_connector import redis_manager

    key = _scrape_running_key(tenant_id)
    existing_task_id = await redis_manager.get(key)
    if existing_task_id:
        existing_result = celery_app.AsyncResult(existing_task_id)
        if not existing_result.ready():
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="A scrape is already running for this tenant.",
            )
        await redis_manager.delete(key)

    from sqlalchemy import select

    from app.models.school import School
    from app.tasks.school_scraper_tasks import sweep_school_media

    school_ids = (
        (await db.execute(select(School.id).where(School.tenant_id == tenant_id)))
        .scalars()
        .all()
    )
    task = sweep_school_media.delay(school_ids=list(school_ids))
    await redis_manager.set(key, task.id, expire=_SCRAPE_RUNNING_TTL)

    return ScrapeAllResponse(
        task_id=task.id,
        status="queued",
        message=(
            f"Scraping {len(school_ids)} district source(s). "
            "Check progress via /school-scraper/scrape-all/status."
        ),
    )


@router.get(
    "/scrape-all/status",
    response_model=ScrapeStatusResponse,
    summary="Check whether a tenant-wide scrape is currently running",
)
async def get_scrape_all_status(
    current_user: User = Depends(get_current_tenant_user),
    tenant_id: int = Depends(get_effective_tenant_id),
) -> ScrapeStatusResponse:
    """Polled by the FE to know when to re-enable the common and per-school
    scrape buttons."""
    from app.celery_app import celery_app
    from app.db.redis_connector import redis_manager

    key = _scrape_running_key(tenant_id)
    task_id = await redis_manager.get(key)
    if not task_id:
        return ScrapeStatusResponse(running=False)

    task_result = celery_app.AsyncResult(task_id)
    if task_result.ready():
        await redis_manager.delete(key)
        return ScrapeStatusResponse(
            running=False, task_id=task_id, task_status=task_result.state
        )

    return ScrapeStatusResponse(
        running=True, task_id=task_id, task_status=task_result.state
    )


@router.post(
    "/preview-transcripts",
    response_model=TranscriptPreviewResponse,
    status_code=status.HTTP_200_OK,
    summary="Preview transcripts for confirmed schools' audio/video/YouTube media (admin)",
)
async def preview_transcripts(
    request: TranscriptPreviewRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_tenant_admin),
    tenant_id: int = Depends(get_effective_tenant_id),
) -> TranscriptPreviewResponse:
    """Scrape every scoped school's confirmed URL and transcribe its
    audio/video/YouTube items via the same `transcription_service` the
    production ingest pipeline uses — but persist nothing.

    Sources confirmed URLs from the DB (no more static JSON input file):
    every active `SchoolScrapeUrl` for this tenant, optionally narrowed by
    `school_ids` or `org_codes`.

    Set `dry_run=True` to list what would be transcribed and estimate cost
    without spending anything. Without it, real transcription runs and
    AssemblyAI charges apply for anything that isn't free (YouTube with
    captions). Nothing is written to `scraped_media`, S3, or Qdrant — use
    `/scrape-media` or `/scrape-all` to actually ingest.
    """
    return await transcript_preview_service.preview(db, tenant_id, request)
