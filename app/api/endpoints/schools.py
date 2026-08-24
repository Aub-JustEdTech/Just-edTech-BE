"""
API endpoints for the school scraping knowledge base.

Provides:
- Schools CRUD + list with filters
- Confirmed scrape URL management per school
- Scraped media listing
"""

import asyncio
import logging
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.endpoints.documents import MIME_BY_EXTENSION
from app.core.config import settings
from app.crud import schools as crud
from app.models.school import School, SchoolScrapeUrl
from app.schemas.schools import (
    DATE_PRESETS,
    SORT_FIELDS,
    STATUS_GROUP_LABELS,
    STATUS_GROUP_RAW_VALUES,
    DatePresetOption,
    SchoolCreate,
    SchoolCandidateReviewListOut,
    SchoolCandidateReviewOut,
    SchoolListOut,
    SchoolOut,
    SchoolScrapeUrlOut,
    SchoolUpdate,
    ScrapedMediaFiltersOut,
    ScrapedMediaListOut,
    ScrapedMediaOut,
    ScrapedMediaStatusGroup,
    ScrapedMediaStatusOption,
    ScrapeUrlCreate,
    ScrapeUrlUpdate,
    SortFieldOption,
)
from app.schemas.users import User
from app.services import school_scrape_url_confirmation_service as confirmation_service
from app.utils.dependencies import (
    get_current_tenant_admin,
    get_db,
    get_effective_tenant_id,
)
from app.utils.s3 import S3Manager

router = APIRouter()
logger = logging.getLogger(__name__)

SCRAPED_MEDIA_S3_URL_EXPIRATION_SECONDS = 3600


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _school_or_404(
    db: AsyncSession, tenant_id: int, school_id: int
) -> School:
    school = await crud.get_school(db, tenant_id, school_id)
    if not school:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"School {school_id} not found in tenant {tenant_id}",
        )
    return school


async def _scrape_url_or_404(
    db: AsyncSession, school_id: int, url_id: int
) -> SchoolScrapeUrl:
    stmt = select(SchoolScrapeUrl).where(
        SchoolScrapeUrl.id == url_id, SchoolScrapeUrl.school_id == school_id
    )
    scrape_url = (await db.execute(stmt)).scalar_one_or_none()
    if not scrape_url:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Scrape URL {url_id} not found for school {school_id}",
        )
    return scrape_url


async def _enrich_school(db: AsyncSession, school: School) -> SchoolOut:
    """Build a SchoolOut with denormalized counts."""
    media_count = await crud.count_scraped_media(db, school.id)
    out = SchoolOut.model_validate(school)
    out.scraped_media_count = media_count
    return out


async def _attach_presigned_s3_urls(media: list[ScrapedMediaOut]) -> None:
    """Populate `s3_url` with a presigned GET URL for each item that has a
    stored S3 copy, preferring the original file (`s3_key_raw`) over the
    transcript (`s3_key_text`) since that's what a document click should open.

    `ResponseContentDisposition=inline` is forced (mirroring the documents.py
    playback flow) so the browser renders the file in a new tab instead of
    downloading it — S3 would otherwise fall back to whatever disposition was
    set at upload time, which for most of these scraped files is "attachment".
    """
    keyed = [
        (item, item.s3_key_raw or item.s3_key_text, bool(item.s3_key_raw))
        for item in media
        if item.s3_key_raw or item.s3_key_text
    ]
    if not keyed:
        return

    s3_manager = S3Manager(
        bucket_name=settings.S3_BUCKET_NAME,
        region_name=settings.S3_REGION,
        aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
        aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
    )

    def _presign(item: ScrapedMediaOut, key: str, is_raw_file: bool):
        safe_name = (item.original_name or f"document_{item.id}").replace('"', "")
        # `file_extension` is only ever set on an original uploaded/downloaded
        # file (s3_key_raw). When there's no raw copy — every YouTube item,
        # and any audio/video item never downloaded — the key we're presigning
        # is the transcript text instead, so the extension-based lookup has
        # nothing to key off and would silently fall back to
        # application/octet-stream, which browsers force-download regardless
        # of the inline Content-Disposition set below.
        content_type = (
            MIME_BY_EXTENSION.get((item.file_extension or "").lower(), "application/octet-stream")
            if is_raw_file
            else MIME_BY_EXTENSION[".transcript"]
        )
        return s3_manager.get_presigned_url(
            s3_key=key,
            expiration=SCRAPED_MEDIA_S3_URL_EXPIRATION_SECONDS,
            response_content_type=content_type,
            response_content_disposition=f'inline; filename="{safe_name}"',
        )

    urls = await asyncio.gather(
        *(_presign(item, key, is_raw_file) for item, key, is_raw_file in keyed),
        return_exceptions=True,
    )
    for (item, key, _is_raw_file), url in zip(keyed, urls):
        if isinstance(url, BaseException):
            logger.warning(
                "Failed to presign s3_url for scraped media %s (key=%s): %s",
                item.id,
                key,
                url,
            )
            continue
        item.s3_url = url


# ---------------------------------------------------------------------------
# Schools CRUD
# ---------------------------------------------------------------------------


@router.get(
    "",
    response_model=SchoolListOut,
    summary="List schools (with scrape URL + last-scrapped metadata)",
)
async def list_schools(
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=500),
    search: str | None = Query(None, description="name or org_code substring"),
    district_type: str | None = Query(None),
    is_active: bool | None = Query(None),
    db: AsyncSession = Depends(get_db),
    tenant_id: int = Depends(get_effective_tenant_id),
) -> SchoolListOut:
    schools, total = await crud.list_schools(
        db,
        tenant_id,
        skip=skip,
        limit=limit,
        search=search,
        district_type=district_type,
        is_active=is_active,
    )
    items = [await _enrich_school(db, school) for school in schools]
    return SchoolListOut(items=items, total=total, skip=skip, limit=limit)


# ---------------------------------------------------------------------------
# Scrape URL confirmation (JSON-backed candidates)
# ---------------------------------------------------------------------------


@router.get(
    "/scrape-url-candidates",
    response_model=SchoolCandidateReviewListOut,
    summary="List schools with offline URL-discovery candidates",
)
async def list_scrape_url_candidates(
    confirmation_status: str | None = Query(
        None,
        description="Filter by confirm state: `added` (has confirmed URL) or `not_added`.",
    ),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=500),
    max_candidates: int = Query(
        100,
        ge=1,
        le=500,
        description="Max ranked candidates per school (default returns full JSON list).",
    ),
    db: AsyncSession = Depends(get_db),
    tenant_id: int = Depends(get_effective_tenant_id),
) -> SchoolCandidateReviewListOut:
    if confirmation_status is not None and confirmation_status not in (
        "added",
        "not_added",
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="confirmation_status must be 'added' or 'not_added'",
        )
    try:
        items, total, added_count, not_added_count = (
            await confirmation_service.list_candidate_reviews(
                db,
                tenant_id,
                confirmation_status=confirmation_status,  # type: ignore[arg-type]
                skip=skip,
                limit=limit,
                max_candidates=max_candidates,
            )
        )
    except FileNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(exc),
        ) from exc

    return SchoolCandidateReviewListOut(
        items=items,
        total=total,
        skip=skip,
        limit=limit,
        added_count=added_count,
        not_added_count=not_added_count,
    )


@router.get(
    "/scraped-media/filters",
    response_model=ScrapedMediaFiltersOut,
    summary="Filter/sort options for the scraped-media list (School Browser)",
)
async def get_scraped_media_filters(
    tenant_id: int = Depends(get_effective_tenant_id),
) -> ScrapedMediaFiltersOut:
    """Static filter metadata for the School Browser's scraped-media list.

    Kept as its own endpoint (rather than hardcoded on the FE) so the status
    grouping / sort fields / date presets have one source of truth — see
    STATUS_GROUP_RAW_VALUES, SORT_FIELDS, DATE_PRESETS in app/schemas/schools.py.
    """
    return ScrapedMediaFiltersOut(
        statuses=[
            ScrapedMediaStatusOption(
                value=group,
                label=STATUS_GROUP_LABELS[group],
                raw_values=raw_values,
            )
            for group, raw_values in STATUS_GROUP_RAW_VALUES.items()
        ],
        sort_fields=[
            SortFieldOption(value=value, label=label) for value, label in SORT_FIELDS
        ],
        date_presets=[
            DatePresetOption(value=value, label=label) for value, label in DATE_PRESETS
        ],
    )


@router.get(
    "/{school_id}",
    response_model=SchoolOut,
    summary="Get school detail",
)
async def get_school(
    school_id: int,
    db: AsyncSession = Depends(get_db),
    tenant_id: int = Depends(get_effective_tenant_id),
) -> SchoolOut:
    school = await _school_or_404(db, tenant_id, school_id)
    return await _enrich_school(db, school)


@router.get(
    "/{school_id}/scrape-url-candidates",
    response_model=SchoolCandidateReviewOut,
    summary="Get ranked URL-discovery candidates for one school",
)
@router.get(
    "/{school_id}/url-candidates",
    response_model=SchoolCandidateReviewOut,
    summary="Alias for scrape-url-candidates (FE backward compatibility)",
    include_in_schema=False,
)
async def get_scrape_url_candidates(
    school_id: int,
    max_candidates: int = Query(
        100,
        ge=1,
        le=500,
        description="Max ranked candidates (default returns full JSON list).",
    ),
    db: AsyncSession = Depends(get_db),
    tenant_id: int = Depends(get_effective_tenant_id),
) -> SchoolCandidateReviewOut:
    await _school_or_404(db, tenant_id, school_id)
    try:
        review = await confirmation_service.get_candidate_review(
            db,
            tenant_id,
            school_id,
            max_candidates=max_candidates,
        )
    except FileNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc

    if review is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No discovery candidates found for this school's org_code",
        )
    return review


@router.post(
    "",
    response_model=SchoolOut,
    status_code=status.HTTP_201_CREATED,
    summary="Create a school (admin)",
)
async def create_school(
    payload: SchoolCreate,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(get_current_tenant_admin),
    tenant_id: int = Depends(get_effective_tenant_id),
) -> SchoolOut:
    school = await crud.create_school(db, tenant_id, payload)
    return await _enrich_school(db, school)


@router.patch(
    "/{school_id}",
    response_model=SchoolOut,
    summary="Update a school (admin)",
)
async def update_school(
    school_id: int,
    payload: SchoolUpdate,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(get_current_tenant_admin),
    tenant_id: int = Depends(get_effective_tenant_id),
) -> SchoolOut:
    school = await _school_or_404(db, tenant_id, school_id)
    school = await crud.update_school(db, school, payload)
    return await _enrich_school(db, school)


@router.delete(
    "/{school_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a school (admin)",
)
async def delete_school(
    school_id: int,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(get_current_tenant_admin),
    tenant_id: int = Depends(get_effective_tenant_id),
) -> None:
    school = await _school_or_404(db, tenant_id, school_id)
    await crud.delete_school(db, school)


# ---------------------------------------------------------------------------
# Scrape URLs per school
# ---------------------------------------------------------------------------


@router.post(
    "/{school_id}/scrape-urls",
    response_model=SchoolScrapeUrlOut,
    status_code=status.HTTP_201_CREATED,
    summary="Add a confirmed scrape URL to a school (admin)",
)
async def add_scrape_url(
    school_id: int,
    payload: ScrapeUrlCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_tenant_admin),
    tenant_id: int = Depends(get_effective_tenant_id),
) -> SchoolScrapeUrlOut:
    school = await _school_or_404(db, tenant_id, school_id)
    url = await crud.add_scrape_url(db, school, payload, current_user.id)
    return SchoolScrapeUrlOut.model_validate(url)


@router.patch(
    "/{school_id}/scrape-urls/{url_id}",
    response_model=SchoolScrapeUrlOut,
    summary="Update a scrape URL (admin)",
)
async def update_scrape_url(
    school_id: int,
    url_id: int,
    payload: ScrapeUrlUpdate,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(get_current_tenant_admin),
    tenant_id: int = Depends(get_effective_tenant_id),
) -> SchoolScrapeUrlOut:
    school = await _school_or_404(db, tenant_id, school_id)
    scrape_url = await _scrape_url_or_404(db, school.id, url_id)
    try:
        updated = await crud.update_scrape_url(db, school, scrape_url, payload)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc)
        ) from exc
    return SchoolScrapeUrlOut.model_validate(updated)


@router.delete(
    "/{school_id}/scrape-urls/{url_id}",
    response_model=SchoolScrapeUrlOut,
    summary="Deactivate a scrape URL (admin)",
)
async def deactivate_scrape_url(
    school_id: int,
    url_id: int,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(get_current_tenant_admin),
    tenant_id: int = Depends(get_effective_tenant_id),
) -> SchoolScrapeUrlOut:
    school = await _school_or_404(db, tenant_id, school_id)
    scrape_url = await _scrape_url_or_404(db, school.id, url_id)
    updated = await crud.deactivate_scrape_url(db, school, scrape_url)
    return SchoolScrapeUrlOut.model_validate(updated)


# ---------------------------------------------------------------------------
# Scraped media
# ---------------------------------------------------------------------------


@router.get(
    "/{school_id}/scraped-media",
    response_model=ScrapedMediaListOut,
    summary="List scraped media for a school",
)
async def list_scraped_media(
    school_id: int,
    status_filter: ScrapedMediaStatusGroup | None = Query(None, alias="status"),
    media_type: str | None = Query(None),
    search: str | None = Query(None),
    date_from: date | None = Query(None),
    date_to: date | None = Query(None),
    sort: str = Query("scraped_at"),
    order: str = Query("desc", pattern="^(asc|desc)$"),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
    tenant_id: int = Depends(get_effective_tenant_id),
) -> ScrapedMediaListOut:
    await _school_or_404(db, tenant_id, school_id)
    status_values = (
        STATUS_GROUP_RAW_VALUES.get(status_filter) if status_filter else None
    )
    items, total = await crud.list_scraped_media(
        db,
        tenant_id,
        school_id=school_id,
        status_values=status_values,
        media_type=media_type,
        search=search,
        date_from=date_from,
        date_to=date_to,
        sort=sort,
        order=order,
        skip=skip,
        limit=limit,
    )
    media_out = [ScrapedMediaOut.model_validate(i) for i in items]
    await _attach_presigned_s3_urls(media_out)
    return ScrapedMediaListOut(
        items=media_out,
        total=total,
        skip=skip,
        limit=limit,
    )
