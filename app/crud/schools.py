"""
CRUD operations for the school scraping knowledge base.

Covers schools, confirmed scrape URLs, scrape runs/jobs, and scraped media.
Functions are async and use the shared AsyncSession from `app.db.connector`.
"""

from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timezone
from urllib.parse import urlsplit, urlunsplit

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.school import (
    School,
    SchoolScrapeJob,
    SchoolScrapeUrl,
    ScrapedMedia,
    ScrapeRun,
)
from app.schemas.schools import (
    SchoolCreate,
    SchoolUpdate,
    ScrapeUrlCreate,
    ScrapeUrlUpdate,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Hashing helpers
# ---------------------------------------------------------------------------


def normalize_url(url: str) -> str:
    """Normalize a URL for stable hashing (strip query, fragment, trailing slash)."""
    parts = urlsplit(url.strip())
    path = parts.path or ""
    if path.endswith("/") and len(path) > 1:
        path = path.rstrip("/")
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, "", ""))


def url_hash(url: str) -> str:
    return hashlib.sha256(normalize_url(url).encode("utf-8")).hexdigest()


def content_hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# ---------------------------------------------------------------------------
# Schools
# ---------------------------------------------------------------------------


async def list_schools(
    db: AsyncSession,
    tenant_id: int,
    *,
    skip: int = 0,
    limit: int = 50,
    search: str | None = None,
    district_type: str | None = None,
    is_active: bool | None = None,
) -> tuple[list[School], int]:
    """Return (schools, total_count) for a tenant with optional filters."""
    stmt = select(School).where(School.tenant_id == tenant_id)
    count_stmt = select(func.count(School.id)).where(School.tenant_id == tenant_id)

    if search:
        like = f"%{search}%"
        stmt = stmt.where(
            (School.name.ilike(like)) | (School.org_code.ilike(like))
        )
        count_stmt = count_stmt.where(
            (School.name.ilike(like)) | (School.org_code.ilike(like))
        )
    if district_type:
        stmt = stmt.where(School.district_type == district_type)
        count_stmt = count_stmt.where(School.district_type == district_type)
    if is_active is not None:
        stmt = stmt.where(School.is_active == is_active)
        count_stmt = count_stmt.where(School.is_active == is_active)

    total = (await db.execute(count_stmt)).scalar_one()
    stmt = (
        stmt.options(selectinload(School.scrape_urls))
        .order_by(School.name.asc())
        .offset(skip)
        .limit(limit)
    )
    items = list((await db.execute(stmt)).scalars().all())
    return items, total


async def get_school(
    db: AsyncSession, tenant_id: int, school_id: int
) -> School | None:
    stmt = (
        select(School)
        .options(selectinload(School.scrape_urls))
        .where(School.id == school_id, School.tenant_id == tenant_id)
    )
    return (await db.execute(stmt)).scalar_one_or_none()


async def get_school_by_org_code(
    db: AsyncSession, tenant_id: int, org_code: str
) -> School | None:
    stmt = (
        select(School)
        .options(selectinload(School.scrape_urls))
        .where(School.org_code == org_code, School.tenant_id == tenant_id)
    )
    return (await db.execute(stmt)).scalar_one_or_none()


async def create_school(
    db: AsyncSession, tenant_id: int, data: SchoolCreate
) -> School:
    school = School(
        tenant_id=data.tenant_id or tenant_id,
        org_code=data.org_code,
        name=data.name,
        district_type=data.district_type,
        website=data.website,
        is_active=data.is_active,
        notes=data.notes,
    )
    db.add(school)
    await db.commit()
    await db.refresh(school)
    return school


async def update_school(
    db: AsyncSession, school: School, data: SchoolUpdate
) -> School:
    for field in ("org_code", "name", "district_type", "website", "is_active", "notes"):
        value = getattr(data, field, None)
        if value is not None:
            setattr(school, field, value)
    await db.commit()
    await db.refresh(school)
    return school


async def delete_school(db: AsyncSession, school: School) -> None:
    await db.delete(school)
    await db.commit()


async def touch_last_scrapped(
    db: AsyncSession, school_id: int, when: datetime | None = None
) -> None:
    """Update School.last_scrapped_at after a scrape completes."""
    when = when or datetime.now(timezone.utc)
    school = await db.get(School, school_id)
    if school:
        school.last_scrapped_at = when
        await db.commit()


# ---------------------------------------------------------------------------
# Scrape URLs
# ---------------------------------------------------------------------------


async def add_scrape_url(
    db: AsyncSession,
    school: School,
    data: ScrapeUrlCreate,
    user_id: int | None,
) -> SchoolScrapeUrl:
    # Idempotent: re-confirm an existing row for the same (school_id, url)
    # instead of INSERTing a duplicate and tripping uq_scrape_url_school_url.
    existing = (
        await db.execute(
            select(SchoolScrapeUrl).where(
                SchoolScrapeUrl.school_id == school.id,
                SchoolScrapeUrl.url == data.url,
            )
        )
    ).scalar_one_or_none()

    if existing is not None:
        existing.crawl_depth = data.crawl_depth
        existing.use_playwright = data.use_playwright
        existing.confirmed_by_user_id = user_id
        existing.confirmed_at = datetime.now(timezone.utc)
        existing.is_active = True
        await db.flush()
        if data.is_primary or school.scrape_url_id is None:
            school.scrape_url_id = existing.id
        await db.commit()
        await db.refresh(existing)
        return existing

    url = SchoolScrapeUrl(
        school_id=school.id,
        url=data.url,
        crawl_depth=data.crawl_depth,
        use_playwright=data.use_playwright,
        confirmed_by_user_id=user_id,
        confirmed_at=datetime.now(timezone.utc),
        is_active=True,
    )
    db.add(url)
    await db.flush()
    if data.is_primary or school.scrape_url_id is None:
        school.scrape_url_id = url.id
    await db.commit()
    await db.refresh(url)
    return url


async def update_scrape_url(
    db: AsyncSession,
    school: School,
    scrape_url: SchoolScrapeUrl,
    data: ScrapeUrlUpdate,
) -> SchoolScrapeUrl:
    if data.crawl_depth is not None:
        scrape_url.crawl_depth = data.crawl_depth
    if data.use_playwright is not None:
        scrape_url.use_playwright = data.use_playwright
    if data.is_active is not None:
        scrape_url.is_active = data.is_active
    if data.is_primary:
        school.scrape_url_id = scrape_url.id
    await db.commit()
    await db.refresh(scrape_url)
    return scrape_url


async def deactivate_scrape_url(
    db: AsyncSession, school: School, scrape_url: SchoolScrapeUrl
) -> SchoolScrapeUrl:
    scrape_url.is_active = False
    if school.scrape_url_id == scrape_url.id:
        school.scrape_url_id = None
    await db.commit()
    await db.refresh(scrape_url)
    return scrape_url


# ---------------------------------------------------------------------------
# Scrape runs & jobs
# ---------------------------------------------------------------------------


async def create_scrape_run(
    db: AsyncSession,
    tenant_id: int,
    *,
    triggered_by: str,
    total_schools: int = 0,
) -> ScrapeRun:
    run = ScrapeRun(
        tenant_id=tenant_id,
        triggered_by=triggered_by,
        status="pending",
        started_at=datetime.now(timezone.utc),
        total_schools=total_schools,
    )
    db.add(run)
    await db.commit()
    await db.refresh(run)
    return run


async def get_scrape_run(
    db: AsyncSession, tenant_id: int, run_id: int
) -> ScrapeRun | None:
    stmt = (
        select(ScrapeRun)
        .options(selectinload(ScrapeRun.jobs))
        .where(ScrapeRun.id == run_id, ScrapeRun.tenant_id == tenant_id)
    )
    return (await db.execute(stmt)).scalar_one_or_none()


async def list_scrape_runs(
    db: AsyncSession,
    tenant_id: int,
    *,
    skip: int = 0,
    limit: int = 20,
) -> tuple[list[ScrapeRun], int]:
    stmt = select(ScrapeRun).where(ScrapeRun.tenant_id == tenant_id)
    count_stmt = select(func.count(ScrapeRun.id)).where(
        ScrapeRun.tenant_id == tenant_id
    )
    total = (await db.execute(count_stmt)).scalar_one()
    stmt = stmt.order_by(ScrapeRun.created_at.desc()).offset(skip).limit(limit)
    items = list((await db.execute(stmt)).scalars().all())
    return items, total


async def finalize_scrape_run(
    db: AsyncSession,
    run_id: int,
    *,
    status: str,
    error_summary: dict | None = None,
) -> ScrapeRun | None:
    run = await db.get(ScrapeRun, run_id)
    if not run:
        return None
    run.status = status
    run.completed_at = datetime.now(timezone.utc)
    if error_summary is not None:
        run.error_summary = error_summary
    await db.commit()
    await db.refresh(run)
    return run


async def aggregate_run_counts(db: AsyncSession, run_id: int) -> ScrapeRun | None:
    """Recompute run-level counts from its jobs."""
    run = await db.get(ScrapeRun, run_id)
    if not run:
        return None
    jobs = (
        await db.execute(
            select(SchoolScrapeJob).where(SchoolScrapeJob.run_id == run_id)
        )
    ).scalars().all()

    run.schools_completed = sum(1 for j in jobs if j.status == "completed")
    run.schools_failed = sum(1 for j in jobs if j.status == "failed")
    run.schools_skipped = sum(1 for j in jobs if j.status == "skipped")
    run.media_found = sum(j.media_found for j in jobs)
    run.media_new = sum(j.media_new for j in jobs)
    run.media_skipped_duplicate = sum(j.media_skipped_duplicate for j in jobs)

    if run.schools_failed == 0 and run.schools_skipped == run.total_schools:
        run.status = "completed"
    elif run.schools_completed + run.schools_skipped == run.total_schools:
        run.status = "partial" if run.schools_failed > 0 else "completed"
    elif all(j.status in ("completed", "failed", "skipped") for j in jobs):
        run.status = "failed" if run.schools_completed == 0 else "partial"
    run.completed_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(run)
    return run


async def create_scrape_job(
    db: AsyncSession,
    *,
    run_id: int,
    school_id: int,
    scrape_url_id: int,
) -> SchoolScrapeJob:
    job = SchoolScrapeJob(
        run_id=run_id,
        school_id=school_id,
        scrape_url_id=scrape_url_id,
        status="pending",
    )
    db.add(job)
    await db.commit()
    await db.refresh(job)
    return job


async def get_scrape_job(db: AsyncSession, job_id: int) -> SchoolScrapeJob | None:
    return await db.get(SchoolScrapeJob, job_id)


async def list_jobs_for_run(
    db: AsyncSession, run_id: int
) -> list[SchoolScrapeJob]:
    stmt = (
        select(SchoolScrapeJob)
        .where(SchoolScrapeJob.run_id == run_id)
        .order_by(SchoolScrapeJob.id.asc())
    )
    return list((await db.execute(stmt)).scalars().all())


# ---------------------------------------------------------------------------
# Scraped media + dedup
# ---------------------------------------------------------------------------


async def get_scraped_media_by_url_hash(
    db: AsyncSession, school_id: int, url_hash_value: str
) -> ScrapedMedia | None:
    stmt = select(ScrapedMedia).where(
        ScrapedMedia.school_id == school_id,
        ScrapedMedia.url_hash == url_hash_value,
    )
    return (await db.execute(stmt)).scalar_one_or_none()


async def get_scraped_media_by_content_hash(
    db: AsyncSession, school_id: int, content_hash_value: str
) -> ScrapedMedia | None:
    stmt = select(ScrapedMedia).where(
        ScrapedMedia.school_id == school_id,
        ScrapedMedia.content_hash == content_hash_value,
    )
    return (await db.execute(stmt)).scalar_one_or_none()


async def list_scraped_media(
    db: AsyncSession,
    tenant_id: int,
    *,
    school_id: int | None = None,
    status: str | None = None,
    media_type: str | None = None,
    skip: int = 0,
    limit: int = 50,
) -> tuple[list[ScrapedMedia], int]:
    stmt = select(ScrapedMedia).where(ScrapedMedia.tenant_id == tenant_id)
    count_stmt = select(func.count(ScrapedMedia.id)).where(
        ScrapedMedia.tenant_id == tenant_id
    )
    if school_id is not None:
        stmt = stmt.where(ScrapedMedia.school_id == school_id)
        count_stmt = count_stmt.where(ScrapedMedia.school_id == school_id)
    if status:
        stmt = stmt.where(ScrapedMedia.status == status)
        count_stmt = count_stmt.where(ScrapedMedia.status == status)
    if media_type:
        stmt = stmt.where(ScrapedMedia.media_type == media_type)
        count_stmt = count_stmt.where(ScrapedMedia.media_type == media_type)
    total = (await db.execute(count_stmt)).scalar_one()
    stmt = stmt.order_by(ScrapedMedia.scraped_at.desc()).offset(skip).limit(limit)
    items = list((await db.execute(stmt)).scalars().all())
    return items, total


async def count_scraped_media(
    db: AsyncSession, school_id: int
) -> int:
    stmt = select(func.count(ScrapedMedia.id)).where(
        ScrapedMedia.school_id == school_id
    )
    return (await db.execute(stmt)).scalar_one()


async def update_scraped_media(
    db: AsyncSession, scraped_media_id: int, **fields
) -> ScrapedMedia | None:
    sm = await db.get(ScrapedMedia, scraped_media_id)
    if not sm:
        return None
    for k, v in fields.items():
        if v is not None or k in ("status",):
            setattr(sm, k, v)
    await db.commit()
    await db.refresh(sm)
    return sm
