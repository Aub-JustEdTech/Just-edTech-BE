"""
CRUD operations for the school scraping knowledge base.

Covers schools, confirmed scrape URLs, and scraped media.
Functions are async and use the shared AsyncSession from `app.db.connector`.
"""

from __future__ import annotations

import hashlib
import logging
from datetime import date, datetime, timezone
from urllib.parse import urlsplit, urlunsplit

from sqlalchemy import asc, desc, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.school import School, SchoolScrapeUrl, ScrapedMedia
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
    """Normalize a URL for stable hashing.

    Strips fragment and trailing slash. Keeps the query string — many CMS
    download endpoints (e.g. SharpSchool ``GetFile.ashx?key=...``) encode the
    document identity only in the query, so dropping it collapses every file
    on a site into one hash.
    """
    parts = urlsplit(url.strip())
    path = parts.path or ""
    if path.endswith("/") and len(path) > 1:
        path = path.rstrip("/")
    return urlunsplit(
        (parts.scheme.lower(), parts.netloc.lower(), path, parts.query, "")
    )


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


async def list_schools_by_org_codes(
    db: AsyncSession, tenant_id: int, org_codes: list[str]
) -> dict[str, School]:
    """Return schools keyed by org_code for the given codes in this tenant."""
    if not org_codes:
        return {}
    stmt = (
        select(School)
        .options(selectinload(School.scrape_urls))
        .where(School.tenant_id == tenant_id, School.org_code.in_(org_codes))
    )
    rows = (await db.execute(stmt)).scalars().all()
    return {school.org_code: school for school in rows}


async def list_active_scrape_urls(
    db: AsyncSession,
    tenant_id: int,
    *,
    school_ids: list[int] | None = None,
    org_codes: list[str] | None = None,
) -> list[SchoolScrapeUrl]:
    """Confirmed, active scrape URLs for a tenant, optionally scoped.

    Each row's `.school` is eagerly loaded so callers (e.g. transcript
    preview) never trigger a lazy load per row.
    """
    stmt = (
        select(SchoolScrapeUrl)
        .join(School, School.id == SchoolScrapeUrl.school_id)
        .options(selectinload(SchoolScrapeUrl.school))
        .where(School.tenant_id == tenant_id, SchoolScrapeUrl.is_active.is_(True))
    )
    if school_ids:
        stmt = stmt.where(SchoolScrapeUrl.school_id.in_(school_ids))
    if org_codes:
        stmt = stmt.where(School.org_code.in_(org_codes))
    stmt = stmt.order_by(School.name.asc())
    return list((await db.execute(stmt)).scalars().all())


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


async def record_scrape_result(
    db: AsyncSession,
    scrape_url: SchoolScrapeUrl,
    *,
    http_status: int | None,
    page_count: int | None,
) -> SchoolScrapeUrl:
    """Persist the outcome of a scrape attempt onto a SchoolScrapeUrl row.

    Also touches the parent School's denormalized `last_scrapped_at` so FE
    list views don't need to fan out over every scrape_urls[] entry.
    """
    when = datetime.now(timezone.utc)
    scrape_url.last_scraped_at = when
    scrape_url.last_http_status = http_status
    scrape_url.last_crawl_page_count = page_count
    school = await db.get(School, scrape_url.school_id)
    if school:
        school.last_scrapped_at = when
    await db.commit()
    await db.refresh(scrape_url)
    return scrape_url


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
    await db.commit()
    await db.refresh(url)
    return url


async def update_scrape_url(
    db: AsyncSession,
    school: School,
    scrape_url: SchoolScrapeUrl,
    data: ScrapeUrlUpdate,
) -> SchoolScrapeUrl:
    if data.url is not None and data.url != scrape_url.url:
        scrape_url.url = data.url
        # The URL text changed, so any prior crawl result no longer applies —
        # treat it as a fresh, unverified page until it's scraped again.
        scrape_url.last_http_status = None
        scrape_url.last_crawl_page_count = None
        scrape_url.last_scraped_at = None
    if data.crawl_depth is not None:
        scrape_url.crawl_depth = data.crawl_depth
    if data.use_playwright is not None:
        scrape_url.use_playwright = data.use_playwright
    if data.is_active is not None:
        scrape_url.is_active = data.is_active
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise ValueError(
            f"URL {data.url!r} is already listed for this school"
        ) from exc
    await db.refresh(scrape_url)
    return scrape_url


async def deactivate_scrape_url(
    db: AsyncSession, school: School, scrape_url: SchoolScrapeUrl
) -> SchoolScrapeUrl:
    scrape_url.is_active = False
    await db.commit()
    await db.refresh(scrape_url)
    return scrape_url


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


async def create_scraped_media(
    db: AsyncSession,
    *,
    school: School,
    source_page_url: str,
    media_file: dict,
    commit: bool = True,
) -> tuple[ScrapedMedia, bool]:
    """Create one scraped_media row idempotently.

    Returns ``(row, created)``. **The ``created`` flag is the cost control:**
    only rows where it is True should be enqueued for transcription. A
    re-crawl of a page with 16 known items and 2 new ones must queue exactly
    2 — without this, every monthly crawl re-pays for the entire corpus.

    YouTube URLs are canonicalised before hashing, so ``youtu.be/X``,
    ``/embed/X`` and ``watch?v=X&t=90`` collapse onto one row.
    """
    from app.services.transcription.youtube import canonical_youtube_url

    raw_url = str(media_file.get("url") or "").strip()
    if not raw_url:
        raise ValueError("media_file has no url")

    media_type = str(media_file.get("media_type") or "document")
    if media_type == "youtube":
        raw_url = canonical_youtube_url(raw_url) or raw_url

    url_hash_value = url_hash(raw_url)

    existing = await get_scraped_media_by_url_hash(db, school.id, url_hash_value)
    if existing:
        return existing, False

    sm = ScrapedMedia(
        tenant_id=school.tenant_id,
        school_id=school.id,
        # Denormalised so downstream consumers and S3 keys do not need a join.
        school_org_code=school.org_code,
        school_name=school.name,
        district_type=getattr(school, "district_type", None),
        source_page_url=source_page_url,
        source_media_url=raw_url,
        url_hash=url_hash_value,
        media_type=media_type,
        file_extension=media_file.get("file_extension"),
        original_name=media_file.get("name"),
        size_bytes=media_file.get("size_bytes"),
        status="discovered",
    )
    db.add(sm)

    if commit:
        await db.commit()
        await db.refresh(sm)
    else:
        await db.flush()

    return sm, True


async def bulk_create_scraped_media(
    db: AsyncSession,
    *,
    school: School,
    source_page_url: str,
    media_files: list[dict],
) -> tuple[list[ScrapedMedia], int]:
    """Create many rows in one commit. Returns ``(created_rows, skipped)``.

    Only the newly created rows come back, so the caller can enqueue exactly
    those and nothing else.
    """
    created: list[ScrapedMedia] = []
    skipped = 0
    # Guards against the same page listing one video twice within a single
    # batch, where neither is in the DB yet.
    seen_hashes: set[str] = set()

    for media_file in media_files:
        raw_url = str(media_file.get("url") or "").strip()
        if not raw_url:
            skipped += 1
            continue

        try:
            row, was_created = await create_scraped_media(
                db,
                school=school,
                source_page_url=source_page_url,
                media_file=media_file,
                commit=False,
            )
        except ValueError:
            skipped += 1
            continue

        if not was_created or row.url_hash in seen_hashes:
            skipped += 1
            continue

        seen_hashes.add(row.url_hash)
        created.append(row)

    await db.commit()
    for row in created:
        await db.refresh(row)

    logger.info(
        "bulk_create_scraped_media school=%s created=%s skipped=%s",
        school.org_code,
        len(created),
        skipped,
    )
    return created, skipped


_SCRAPED_MEDIA_SORT_COLUMNS = {
    "scraped_at": ScrapedMedia.scraped_at,
    "original_name": ScrapedMedia.original_name,
    "size_bytes": ScrapedMedia.size_bytes,
    "status": ScrapedMedia.status,
}


async def list_scraped_media(
    db: AsyncSession,
    tenant_id: int,
    *,
    school_id: int | None = None,
    status_values: list[str] | None = None,
    media_type: str | None = None,
    search: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    sort: str = "scraped_at",
    order: str = "desc",
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
    if status_values:
        stmt = stmt.where(ScrapedMedia.status.in_(status_values))
        count_stmt = count_stmt.where(ScrapedMedia.status.in_(status_values))
    if media_type:
        stmt = stmt.where(ScrapedMedia.media_type == media_type)
        count_stmt = count_stmt.where(ScrapedMedia.media_type == media_type)
    if search:
        term = f"%{search}%"
        search_clause = or_(
            ScrapedMedia.original_name.ilike(term),
            ScrapedMedia.source_media_url.ilike(term),
        )
        stmt = stmt.where(search_clause)
        count_stmt = count_stmt.where(search_clause)
    if date_from is not None:
        stmt = stmt.where(func.date(ScrapedMedia.scraped_at) >= date_from)
        count_stmt = count_stmt.where(func.date(ScrapedMedia.scraped_at) >= date_from)
    if date_to is not None:
        stmt = stmt.where(func.date(ScrapedMedia.scraped_at) <= date_to)
        count_stmt = count_stmt.where(func.date(ScrapedMedia.scraped_at) <= date_to)

    total = (await db.execute(count_stmt)).scalar_one()

    sort_column = _SCRAPED_MEDIA_SORT_COLUMNS.get(sort, ScrapedMedia.scraped_at)
    direction = asc if order == "asc" else desc
    stmt = stmt.order_by(direction(sort_column)).offset(skip).limit(limit)
    items = list((await db.execute(stmt)).scalars().all())
    return items, total


async def count_scraped_media(
    db: AsyncSession, school_id: int
) -> int:
    stmt = select(func.count(ScrapedMedia.id)).where(
        ScrapedMedia.school_id == school_id
    )
    return (await db.execute(stmt)).scalar_one()


async def list_skipped_year_media(
    db: AsyncSession,
    tenant_id: int | None = None,
    school_id: int | None = None,
) -> list[ScrapedMedia]:
    """Return all scraped_media rows with status='skipped_year'.

    Used by the backfill-years endpoint to re-evaluate rows against a
    widened SCHOOL_SCRAPER_ALLOWED_YEARS set without re-crawling the site.
    Optionally scoped to a single school or tenant.
    """
    stmt = select(ScrapedMedia).where(ScrapedMedia.status == "skipped_year")
    if tenant_id is not None:
        stmt = stmt.where(ScrapedMedia.tenant_id == tenant_id)
    if school_id is not None:
        stmt = stmt.where(ScrapedMedia.school_id == school_id)
    stmt = stmt.order_by(ScrapedMedia.id.asc())
    return list((await db.execute(stmt)).scalars().all())


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
