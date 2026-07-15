"""
API endpoints for the school scraping knowledge base.

Provides:
- Schools CRUD + list with filters
- Confirmed scrape URL management per school
- Stateless URL discovery (for one-time FE confirmation)
- Manual scrape triggers (per-school and full-cycle)
- Scrape run listing + detail
- Scraped media listing
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.crud import school_url_discovery as discovery_crud
from app.crud import schools as crud
from app.models.school import School, SchoolScrapeJob, SchoolScrapeUrl
from app.schemas.school_scraper import (
    CandidateUrl,
    DiscoverRequest,
    DiscoverResponse,
)
from app.schemas.schools import (
    SchoolCreate,
    SchoolListOut,
    SchoolOut,
    SchoolScrapeJobOut,
    SchoolScrapeUrlOut,
    SchoolUpdate,
    SchoolUrlCandidateOut,
    SchoolUrlCandidatesOut,
    ScrapedMediaListOut,
    ScrapedMediaOut,
    ScrapeJobAck,
    ScrapeRunDetailOut,
    ScrapeRunListOut,
    ScrapeRunOut,
    ScrapeUrlCreate,
    ScrapeUrlUpdate,
    TriggerRunRequest,
    TriggerSchoolScrapeRequest,
)
from app.schemas.users import User
from app.services.web_scraper.school_scraper_service import SchoolScraperService
from app.utils.dependencies import (
    get_current_tenant_admin,
    get_current_tenant_user,
    get_db,
)

router = APIRouter()
logger = logging.getLogger(__name__)


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


    return scrape_url


def _confirmed_scrape_url(school: School) -> str | None:
    if not school.scrape_url_id:
        return None
    for scrape_url in school.scrape_urls:
        if scrape_url.id == school.scrape_url_id and scrape_url.is_active:
            return scrape_url.url
    return None


def _url_discovery_status(
    school: School,
    *,
    candidate_count: int = 0,
    error: str | None = None,
) -> str:
    if school.scrape_url_id:
        return "confirmed"
    if error:
        return "error"
    if candidate_count > 0:
        return "discovered"
    return "not_discovered"


async def _enrich_school(
    db: AsyncSession,
    school: School,
    *,
    discovery_summary: dict[str, object] | None = None,
) -> SchoolOut:
    """Build a SchoolOut with denormalized counts/status."""
    media_count = await crud.count_scraped_media(db, school.id)
    stmt = (
        select(SchoolScrapeJob)
        .where(SchoolScrapeJob.school_id == school.id)
        .order_by(SchoolScrapeJob.created_at.desc())
        .limit(1)
    )
    last_job = (await db.execute(stmt)).scalar_one_or_none()
    out = SchoolOut.model_validate(school)
    out.scraped_media_count = media_count
    out.last_run_status = last_job.status if last_job else None
    out.confirmed_scrape_url = _confirmed_scrape_url(school)

    if discovery_summary is None:
        discovery_summary = {}

    candidate_count = int(discovery_summary.get("candidate_count") or 0)
    error = discovery_summary.get("error")
    out.url_candidate_count = candidate_count
    out.url_discovery_status = _url_discovery_status(
        school,
        candidate_count=candidate_count,
        error=str(error) if error else None,
    )
    return out


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
    current_user: User = Depends(get_current_tenant_user),
) -> SchoolListOut:
    schools, total = await crud.list_schools(
        db,
        current_user.tenant_id,
        skip=skip,
        limit=limit,
        search=search,
        district_type=district_type,
        is_active=is_active,
    )
    summaries = await discovery_crud.get_discovery_summaries(
        db,
        current_user.tenant_id,
        [school.id for school in schools],
    )
    items = [
        await _enrich_school(db, school, discovery_summary=summaries.get(school.id))
        for school in schools
    ]
    return SchoolListOut(items=items, total=total, skip=skip, limit=limit)


@router.get(
    "/{school_id}",
    response_model=SchoolOut,
    summary="Get school detail",
)
async def get_school(
    school_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_tenant_user),
) -> SchoolOut:
    school = await _school_or_404(db, current_user.tenant_id, school_id)
    summaries = await discovery_crud.get_discovery_summaries(
        db, current_user.tenant_id, [school.id]
    )
    return await _enrich_school(
        db, school, discovery_summary=summaries.get(school.id)
    )


@router.post(
    "",
    response_model=SchoolOut,
    status_code=status.HTTP_201_CREATED,
    summary="Create a school (admin)",
)
async def create_school(
    payload: SchoolCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_tenant_admin),
) -> SchoolOut:
    school = await crud.create_school(db, current_user.tenant_id, payload)
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
    current_user: User = Depends(get_current_tenant_admin),
) -> SchoolOut:
    school = await _school_or_404(db, current_user.tenant_id, school_id)
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
    current_user: User = Depends(get_current_tenant_admin),
) -> None:
    school = await _school_or_404(db, current_user.tenant_id, school_id)
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
) -> SchoolScrapeUrlOut:
    school = await _school_or_404(db, current_user.tenant_id, school_id)
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
    current_user: User = Depends(get_current_tenant_admin),
) -> SchoolScrapeUrlOut:
    school = await _school_or_404(db, current_user.tenant_id, school_id)
    scrape_url = await _scrape_url_or_404(db, school.id, url_id)
    updated = await crud.update_scrape_url(db, school, scrape_url, payload)
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
    current_user: User = Depends(get_current_tenant_admin),
) -> SchoolScrapeUrlOut:
    school = await _school_or_404(db, current_user.tenant_id, school_id)
    scrape_url = await _scrape_url_or_404(db, school.id, url_id)
    updated = await crud.deactivate_scrape_url(db, school, scrape_url)
    return SchoolScrapeUrlOut.model_validate(updated)


# ---------------------------------------------------------------------------
# Stored URL candidates
# ---------------------------------------------------------------------------


@router.get(
    "/{school_id}/url-candidates",
    response_model=SchoolUrlCandidatesOut,
    summary="Get stored URL-discovery candidates for a school",
)
async def get_school_url_candidates(
    school_id: int,
    max_candidates: int = Query(
        settings.SCHOOL_SCRAPER_MAX_CANDIDATES,
        ge=1,
        le=50,
        description="Maximum number of deduplicated candidates to return.",
    ),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_tenant_user),
) -> SchoolUrlCandidatesOut:
    school = await _school_or_404(db, current_user.tenant_id, school_id)
    discovery, candidates = await discovery_crud.list_candidates_for_school(
        db,
        current_user.tenant_id,
        school.id,
        max_candidates=max_candidates,
    )

    error = discovery.error if discovery else None
    candidate_count = len(candidates)
    status = _url_discovery_status(
        school,
        candidate_count=candidate_count,
        error=error,
    )

    return SchoolUrlCandidatesOut(
        school_id=school.id,
        org_code=school.org_code,
        name=school.name,
        website=school.website,
        discovery_method=discovery.discovery_method if discovery else None,
        total_urls_scanned=discovery.total_urls_scanned if discovery else 0,
        error=error,
        url_discovery_status=status,
        confirmed_scrape_url=_confirmed_scrape_url(school),
        total_candidates=candidate_count,
        candidates=[
            SchoolUrlCandidateOut(
                url=row.url,
                matched_keywords=list(row.matched_keywords or []),
                score=row.score,
                rank=row.rank,
            )
            for row in candidates
        ],
    )


# ---------------------------------------------------------------------------
# Discovery (stateless)
# ---------------------------------------------------------------------------


@router.post(
    "/{school_id}/discover",
    response_model=DiscoverResponse,
    summary="Discover candidate archive URLs for a school (stateless)",
)
async def discover_for_school(
    school_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_tenant_user),
) -> DiscoverResponse:
    school = await _school_or_404(db, current_user.tenant_id, school_id)
    if not school.website:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="School has no website configured; cannot discover URLs.",
        )
    request = DiscoverRequest(base_url=school.website)
    async with SchoolScraperService() as svc:
        try:
            result = await svc.discover_candidate_urls(
                base_url=request.base_url,
                max_candidates=request.max_candidates,
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("URL discovery failed for school %s", school_id)
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Discovery failed: {exc}",
            ) from exc
    candidates = [
        CandidateUrl(
            url=c["url"],
            matched_keywords=c["matched_keywords"],
            score=c["score"],
        )
        for c in result.get("candidates", [])
    ]
    return DiscoverResponse(
        base_url=result["base_url"],
        discovery_method=result["discovery_method"],
        total_urls_scanned=result["total_urls_scanned"],
        total_candidates=len(candidates),
        candidates=candidates,
    )


# ---------------------------------------------------------------------------
# Scrape triggers
# ---------------------------------------------------------------------------


@router.post(
    "/{school_id}/scrape-now",
    response_model=ScrapeJobAck,
    summary="Manually trigger a scrape for a single school (admin)",
)
async def scrape_school_now(
    school_id: int,
    payload: TriggerSchoolScrapeRequest | None = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_tenant_admin),
) -> ScrapeJobAck:
    from app.tasks.school_scraper_tasks import run_single_school_scrape

    school = await _school_or_404(db, current_user.tenant_id, school_id)
    scrape_url_id = (payload.scrape_url_id if payload else None) or school.scrape_url_id
    if not scrape_url_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No confirmed scrape URL configured for this school.",
        )
    run = await crud.create_scrape_run(
        db,
        current_user.tenant_id,
        triggered_by=f"manual:user:{current_user.id}",
        total_schools=1,
    )
    job = await crud.create_scrape_job(
        db,
        run_id=run.id,
        school_id=school.id,
        scrape_url_id=scrape_url_id,
    )
    run_single_school_scrape.delay(
        job_id=job.id,
        school_id=school.id,
        scrape_url_id=scrape_url_id,
        tenant_id=current_user.tenant_id,
    )
    return ScrapeJobAck(
        run_id=run.id,
        job_id=job.id,
        status="pending",
        message="School scrape enqueued.",
    )


@router.post(
    "/scrape-runs/trigger",
    response_model=ScrapeJobAck,
    summary="Manually trigger a full scrape cycle for the tenant (admin)",
)
async def trigger_full_cycle(
    payload: TriggerRunRequest | None = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_tenant_admin),
) -> ScrapeJobAck:
    from app.tasks.school_scraper_tasks import run_school_scrape_cycle

    only_active = (payload.only_active if payload else True)
    run = await crud.create_scrape_run(
        db,
        current_user.tenant_id,
        triggered_by=f"manual:user:{current_user.id}",
    )
    run_school_scrape_cycle.delay(run_id=run.id, only_active=only_active)
    return ScrapeJobAck(
        run_id=run.id,
        job_id=None,
        status="pending",
        message="Full scrape cycle enqueued.",
    )


# ---------------------------------------------------------------------------
# Scrape runs
# ---------------------------------------------------------------------------


@router.get(
    "/scrape-runs",
    response_model=ScrapeRunListOut,
    summary="List scrape runs",
)
async def list_scrape_runs(
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_tenant_user),
) -> ScrapeRunListOut:
    runs, total = await crud.list_scrape_runs(
        db, current_user.tenant_id, skip=skip, limit=limit
    )
    items = [ScrapeRunOut.model_validate(r) for r in runs]
    return ScrapeRunListOut(items=items, total=total, skip=skip, limit=limit)


@router.get(
    "/scrape-runs/{run_id}",
    response_model=ScrapeRunDetailOut,
    summary="Get scrape run detail with per-school jobs",
)
async def get_scrape_run(
    run_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_tenant_user),
) -> ScrapeRunDetailOut:
    run = await crud.get_scrape_run(db, current_user.tenant_id, run_id)
    if not run:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Scrape run not found"
        )
    detail = ScrapeRunDetailOut.model_validate(run)
    detail.jobs = [SchoolScrapeJobOut.model_validate(j) for j in run.jobs]
    return detail


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
    status_filter: str | None = Query(None, alias="status"),
    media_type: str | None = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_tenant_user),
) -> ScrapedMediaListOut:
    await _school_or_404(db, current_user.tenant_id, school_id)
    items, total = await crud.list_scraped_media(
        db,
        current_user.tenant_id,
        school_id=school_id,
        status=status_filter,
        media_type=media_type,
        skip=skip,
        limit=limit,
    )
    return ScrapedMediaListOut(
        items=[ScrapedMediaOut.model_validate(i) for i in items],
        total=total,
        skip=skip,
        limit=limit,
    )
