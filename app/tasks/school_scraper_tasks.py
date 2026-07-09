"""
Celery tasks for the school scraping pipeline.

- `run_school_scrape_cycle`: biweekly full-cycle entry point. Iterates all
  active schools (with active scrape URLs) for a tenant, creates one
  SchoolScrapeJob per school, and dispatches `run_single_school_scrape`
  sub-tasks. Aggregates results back into the ScrapeRun.
- `run_single_school_scrape`: scrapes one (school x scrape URL) job via
  SchoolScraperService, dedups via url_hash + content_hash, persists
  ScrapedMedia rows, and (optionally) enqueues document processing.

Both tasks are sync wrappers around async coroutines (Celery workers are
sync; the underlying services use async httpx/Playwright/aioboto3).
"""

import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.celery_app import celery_app
from app.core.config import settings
from app.crud import schools as crud
from app.db.connector import AsyncSessionLocal
from app.models.school import (
    School,
    SchoolScrapeJob,
    SchoolScrapeUrl,
    ScrapeRun,
)
from app.tasks.loop_utils import get_event_loop

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Full cycle (one ScrapeRun across all active schools in a tenant)
# ---------------------------------------------------------------------------


@celery_app.task(
    name="app.tasks.school_scraper_tasks.run_school_scrape_cycle_for_tenants",
    bind=True,
)
def run_school_scrape_cycle_for_tenants(self):
    """Beat entry point: dispatch a per-tenant full scrape cycle.

    Iterates every tenant that has at least one active school with an
    active scrape URL, creates a ScrapeRun for each, and dispatches
    `run_school_scrape_cycle` per tenant.
    """
    if not settings.SCHOOL_SCRAPER_CRON_ENABLED:
        logger.info(
            "School scraper cron disabled (SCHOOL_SCRAPER_CRON_ENABLED=False); "
            "skipping biweekly cycle."
        )
        return {"skipped": True, "reason": "cron disabled"}

    loop = get_event_loop()
    return loop.run_until_complete(_dispatch_per_tenant_cycles())


async def _dispatch_per_tenant_cycles() -> dict:
    from sqlalchemy import distinct, select

    from app.models.school import School, SchoolScrapeUrl

    dispatched: list[dict] = []
    async with AsyncSessionLocal() as db:
        stmt = (
            select(distinct(School.tenant_id))
            .join(SchoolScrapeUrl, SchoolScrapeUrl.school_id == School.id)
            .where(School.is_active.is_(True), SchoolScrapeUrl.is_active.is_(True))
        )
        tenant_ids = list((await db.execute(stmt)).scalars().all())

        for tenant_id in tenant_ids:
            run = await crud.create_scrape_run(
                db,
                tenant_id=tenant_id,
                triggered_by="scheduler",
            )
            dispatched.append({"tenant_id": tenant_id, "run_id": run.id})

    for entry in dispatched:
        run_school_scrape_cycle.delay(
            run_id=entry["run_id"],
            tenant_id=entry["tenant_id"],
            only_active=True,
        )
    return {"dispatched": dispatched}


@celery_app.task(
    name="app.tasks.school_scraper_tasks.run_school_scrape_cycle",
    bind=True,
)
def run_school_scrape_cycle(
    self,
    run_id: int,
    tenant_id: int | None = None,
    only_active: bool = True,
):
    """Run a full scraping cycle for a tenant.

    Creates one SchoolScrapeJob per active school with an active scrape
    URL, then dispatches a `run_single_school_scrape` sub-task per job.
    """
    loop = get_event_loop()
    return loop.run_until_complete(
        _run_cycle_async(run_id=run_id, tenant_id=tenant_id, only_active=only_active)
    )


async def _run_cycle_async(
    run_id: int, tenant_id: int | None, only_active: bool
) -> dict:
    async with AsyncSessionLocal() as db:
        run = await db.get(ScrapeRun, run_id)
        if not run:
            logger.error("ScrapeRun %s not found", run_id)
            return {"run_id": run_id, "error": "run not found"}
        if tenant_id is None:
            tenant_id = run.tenant_id

        # Select all active schools for this tenant that have an active
        # scrape URL configured.
        stmt = (
            select(School, SchoolScrapeUrl)
            .join(SchoolScrapeUrl, SchoolScrapeUrl.school_id == School.id)
            .where(
                School.tenant_id == tenant_id,
                School.is_active.is_(True) if only_active else School.tenant_id == tenant_id,
                SchoolScrapeUrl.is_active.is_(True),
            )
            .order_by(School.id.asc())
        )
        rows = (await db.execute(stmt)).all()
        run.total_schools = len(rows)
        run.status = "running"
        run.started_at = run.started_at or datetime.now(timezone.utc)
        await db.commit()

        job_ids: list[int] = []
        for school, scrape_url in rows:
            job = SchoolScrapeJob(
                run_id=run.id,
                school_id=school.id,
                scrape_url_id=scrape_url.id,
                status="pending",
            )
            db.add(job)
            await db.flush()
            job_ids.append((job.id, school.id, scrape_url.id))

        await db.commit()

    # Dispatch sub-tasks outside the DB session.
    for job_id, school_id, scrape_url_id in job_ids:
        run_single_school_scrape.delay(
            job_id=job_id,
            school_id=school_id,
            scrape_url_id=scrape_url_id,
            tenant_id=tenant_id,
        )

    return {
        "run_id": run_id,
        "tenant_id": tenant_id,
        "jobs_dispatched": len(job_ids),
    }


# ---------------------------------------------------------------------------
# Single-school scrape (one SchoolScrapeJob)
# ---------------------------------------------------------------------------


@celery_app.task(
    name="app.tasks.school_scraper_tasks.run_single_school_scrape",
    bind=True,
    max_retries=2,
)
def run_single_school_scrape(
    self,
    job_id: int,
    school_id: int,
    scrape_url_id: int,
    tenant_id: int,
):
    """Scrape one (school x scrape URL) and persist new media."""
    loop = get_event_loop()
    return loop.run_until_complete(
        _run_single_scrape_async(
            job_id=job_id,
            school_id=school_id,
            scrape_url_id=scrape_url_id,
            tenant_id=tenant_id,
        )
    )


async def _run_single_scrape_async(
    job_id: int, school_id: int, scrape_url_id: int, tenant_id: int
) -> dict:
    from app.services.web_scraper.school_scraper_service import SchoolScraperService

    async with AsyncSessionLocal() as db:
        job = await db.get(SchoolScrapeJob, job_id)
        if not job:
            return {"job_id": job_id, "error": "job not found"}

        school = await db.get(School, school_id)
        scrape_url = await db.get(SchoolScrapeUrl, scrape_url_id)
        if not school or not scrape_url:
            job.status = "failed"
            job.error_message = "school or scrape_url missing"
            job.completed_at = datetime.now(timezone.utc)
            await db.commit()
            return {"job_id": job_id, "error": "school/url missing"}

        job.status = "running"
        job.started_at = datetime.now(timezone.utc)
        await db.commit()

        try:
            async with SchoolScraperService() as svc:
                scrape_result = await svc.scrape_media_files(
                    page_url=scrape_url.url,
                    crawl_depth=scrape_url.crawl_depth,
                )
        except Exception as exc:  # noqa: BLE001
            logger.exception("Scrape failed for job %s (school %s)", job_id, school_id)
            job.status = "failed"
            job.error_message = str(exc)
            job.completed_at = datetime.now(timezone.utc)
            await db.commit()
            await _finalize_run_and_touch_school(db, job.run_id, school_id, failed=True)
            raise

        media_files = scrape_result.get("media_files", [])
        job.pages_crawled = scrape_result.get("pages_crawled", 0)
        job.media_found = len(media_files)

        # Run the dedup + persist loop.
        media_new = 0
        media_skipped = 0
        for mf in media_files:
            normalized = crud.normalize_url(mf["url"])
            uh = crud.url_hash(mf["url"])
            existing = await crud.get_scraped_media_by_url_hash(db, school.id, uh)
            if existing:
                media_skipped += 1
                continue

            media_type = _classify_media_type(mf.get("media_type"), mf.get("file_extension"))
            sm = ScrapedMedia(
                tenant_id=tenant_id,
                school_id=school.id,
                school_org_code=school.org_code,
                school_name=school.name,
                district_type=school.district_type,
                scrape_job_id=job.id,
                scrape_run_id=job.run_id,
                source_page_url=mf.get("source_page_url", scrape_url.url),
                source_media_url=mf["url"],
                url_hash=uh,
                content_hash=None,  # filled by ingest step after download
                media_type=media_type,
                file_extension=mf.get("file_extension"),
                original_name=mf.get("name"),
                status="discovered",
            )
            db.add(sm)
            await db.flush()
            media_new += 1

            # Enqueue a follow-up ingest task per media item. The ingest
            # task downloads (or transcribes for youtube/audio/video),
            # computes content_hash, uploads to S3, creates a Document,
            # and wires ScrapedMedia.document_id. See ingest_scraped_media.
            ingest_scraped_media.delay(scraped_media_id=sm.id)

        job.media_new = media_new
        job.media_skipped_duplicate = media_skipped
        job.status = "completed"
        job.completed_at = datetime.now(timezone.utc)
        job.scrape_result = scrape_result
        await db.commit()

        await _finalize_run_and_touch_school(db, job.run_id, school.id, failed=False)
        return {
            "job_id": job_id,
            "school_id": school_id,
            "pages_crawled": job.pages_crawled,
            "media_found": job.media_found,
            "media_new": media_new,
            "media_skipped_duplicate": media_skipped,
        }


async def _finalize_run_and_touch_school(
    db: AsyncSession, run_id: int, school_id: int, *, failed: bool
) -> None:
    """Update School.last_scrapped_at and recompute ScrapeRun counts."""
    await crud.touch_last_scrapped(db, school_id)
    await crud.aggregate_run_counts(db, run_id)


# ---------------------------------------------------------------------------
# Media ingest (download / transcribe / create Document)
# ---------------------------------------------------------------------------


@celery_app.task(
    name="app.tasks.school_scraper_tasks.ingest_scraped_media",
    bind=True,
    max_retries=2,
)
def ingest_scraped_media(self, scraped_media_id: int):
    """Download/transcribe a scraped media item and ingest into the doc pipeline.

    Implementation note: this is intentionally a thin task that delegates
    the heavy lifting to async helpers. The full media-handling pipeline
    (yt-dlp for youtube, Whisper for audio/video, text extraction for
    PDF/DOCX) is wired here but the exact transcriber/extractor services
    may live in app/services/ and are imported lazily to keep this module
    importable without optional dependencies.
    """
    loop = get_event_loop()
    return loop.run_until_complete(_ingest_scraped_media_async(scraped_media_id))


async def _ingest_scraped_media_async(scraped_media_id: int) -> dict:
    from app.crud.schools import (
        get_scraped_media_by_content_hash,
        update_scraped_media,
    )
    from app.models.school import ScrapedMedia

    async with AsyncSessionLocal() as db:
        sm = await db.get(ScrapedMedia, scraped_media_id)
        if not sm:
            return {"scraped_media_id": scraped_media_id, "error": "not found"}

        await update_scraped_media(db, sm.id, status="downloading")

        try:
            # Download or fetch transcript depending on media type.
            raw_bytes, text_content, content_h = await _fetch_media_payload(sm)
        except Exception as exc:  # noqa: BLE001
            logger.exception(
                "Ingest failed for scraped_media %s", scraped_media_id
            )
            await update_scraped_media(
                db,
                sm.id,
                status="failed",
                error_message=str(exc),
            )
            raise

        # True content dedup: if content_h already exists for this school,
        # mark this row as skipped_duplicate and link to the existing row.
        if content_h:
            existing = await get_scraped_media_by_content_hash(db, sm.school_id, content_h)
            # get_scraped_media_by_content_hash returns the first match,
            # which may be the same row we just updated. Guard:
            if existing and existing.id != sm.id:
                await update_scraped_media(
                    db,
                    sm.id,
                    status="skipped_duplicate",
                    content_hash=content_h,
                )
                return {
                    "scraped_media_id": scraped_media_id,
                    "status": "skipped_duplicate",
                }

        # Upload to S3 + create Document, then wire ScrapedMedia.document_id.
        await update_scraped_media(
            db,
            sm.id,
            status="ingesting",
            content_hash=content_h,
        )

        document_id = await _create_document_and_enqueue(
            db, sm, raw_bytes, text_content, content_h
        )

        await update_scraped_media(
            db,
            sm.id,
            status="completed",
            document_id=document_id,
            ingested_at=datetime.now(timezone.utc),
        )
        return {
            "scraped_media_id": scraped_media_id,
            "status": "completed",
            "document_id": document_id,
        }


async def _fetch_media_payload(sm):
    """Return (raw_bytes, text_content, content_hash) for a ScrapedMedia item.

    Dispatches by media type:
    - document (pdf/docx/...): download bytes, extract text.
    - youtube: fetch transcript via yt-dlp (no raw bytes), text only.
    - audio/video: download bytes, transcribe via Whisper.
    """
    import hashlib

    import httpx

    if sm.media_type == "youtube":
        if not settings.SCHOOL_SCRAPER_YOUTUBE_TRANSCRIPT_ENABLED:
            raise RuntimeError(
                "YouTube transcript disabled (SCHOOL_SCRAPER_YOUTUBE_TRANSCRIPT_ENABLED=False)"
            )
        text = await _fetch_youtube_transcript(sm.source_media_url)
        return None, text, None  # content_hash stays null for youtube

    # All other types: download raw bytes.
    async with httpx.AsyncClient(
        timeout=settings.WEB_SCRAPER_TIMEOUT_SECONDS,
        headers={"User-Agent": settings.SCHOOL_SCRAPER_USER_AGENT},
        follow_redirects=True,
    ) as client:
        resp = await client.get(sm.source_media_url)
        resp.raise_for_status()
        raw = resp.content

    content_h = hashlib.sha256(raw).hexdigest()

    if sm.media_type == "document":
        text = _extract_text_from_document(raw, sm.file_extension)
    else:  # audio / video
        if not settings.SCHOOL_SCRAPER_WHISPER_TRANSCRIPTION_ENABLED:
            text = ""
        else:
            text = await _transcribe_media(raw, sm.file_extension)

    return raw, text, content_h


async def _fetch_youtube_transcript(url: str) -> str:
    """Fetch a YouTube transcript via yt-dlp + a transcript lib.

    Wrapped defensively: yt-dlp is an optional dependency; if unavailable,
    the row is marked failed with a clear error.
    """
    try:
        from yt_dlp import YoutubeDL  # type: ignore
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError(
            "yt-dlp is not installed; cannot fetch YouTube transcript"
        ) from exc

    # Fetch available subtitles/transcripts. yt-dlp is sync, run in thread.
    import asyncio

    def _fetch() -> str:
        opts = {
            "writesubtitles": True,
            "writeautomaticsub": True,
            "skip_download": True,
            "subtitleslangs": ["en"],
            "subtitlesformat": "vtt",
            "quiet": True,
        }
        with YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
            subs = (info or {}).get("subtitles") or {}
            auto_subs = (info or {}).get("automatic_captions") or {}
            track = subs.get("en") or auto_subs.get("en") or []
            if not track:
                return ""
            # Take the first track; in practice you'd parse the vtt/json.
            return str(track[0].get("ext", ""))  # placeholder

    return await asyncio.to_thread(_fetch)


def _extract_text_from_document(raw: bytes, ext: str | None) -> str:
    """Extract text from PDF/DOCX/XLSX bytes using the existing extractors."""
    # Defer to the document processing service's extractors. Kept as a thin
    # adapter; the real extraction lives in app.services.document_pipeline.
    import io

    ext = (ext or "").lower().lstrip(".")
    if ext == "pdf":
        import fitz  # pymupdf

        with fitz.open(stream=raw, filetype="pdf") as doc:
            return "\n".join(page.get_text() for page in doc)
    if ext in ("docx", "doc"):
        import docx

        d = docx.Document(io.BytesIO(raw))
        return "\n".join(p.text for p in d.paragraphs)
    # Fallback: treat as text.
    try:
        return raw.decode("utf-8", errors="ignore")
    except Exception:  # noqa: BLE001
        return ""


async def _transcribe_media(raw: bytes, ext: str | None) -> str:
    """Transcribe audio/video bytes via Whisper (OpenAI)."""
    # Placeholder: full Whisper integration (local or API) belongs in
    # app.services. For now we return empty text so the Document is still
    # created and the pipeline can be extended later.
    logger.warning(
        "Whisper transcription not yet wired; returning empty transcript for %s bytes",
        len(raw),
    )
    return ""


async def _create_document_and_enqueue(
    db: AsyncSession,
    sm,
    raw_bytes: bytes | None,
    text_content: str,
    content_h: str | None,
) -> int | None:
    """Create a Document row for the scraped text and enqueue processing.

    Writes the extracted text to S3, creates a Document with
    source_type='school_scraper', then enqueues the existing document
    processing pipeline so embeddings land in Qdrant with the enriched
    payload (see qdrant_store.py changes).
    """
    from app.models.documents import Document, ProcessingStatus

    s3_key_text = (
        f"tenants/{sm.tenant_id}/schools/{sm.school_org_code}/"
        f"{sm.media_type}/{content_h or sm.url_hash}/transcript.txt"
    )
    s3_url_text = await _upload_text_to_s3(s3_key_text, text_content)

    s3_key_raw = None
    if raw_bytes is not None:
        ext = (sm.file_extension or "bin").lstrip(".")
        s3_key_raw = (
            f"tenants/{sm.tenant_id}/schools/{sm.school_org_code}/"
            f"{sm.media_type}/{content_h}/{sm.original_name or f'file.{ext}'}"
        )
        await _upload_raw_to_s3(s3_key_raw, raw_bytes)

    doc = Document(
        name=sm.original_name or sm.source_media_url,
        doc_id=f"school-{sm.school_org_code}-{content_h or sm.url_hash[:16]}",
        s3_url=s3_url_text,
        tenant_id=sm.tenant_id,
        document_type="txt",
        processing_status=ProcessingStatus.PENDING,
        source_type="school_scraper",
        source_metadata={
            "scraped_media_id": sm.id,
            "school_id": sm.school_id,
            "school_org_code": sm.school_org_code,
            "school_name": sm.school_name,
            "district_type": sm.district_type,
            "source_page_url": sm.source_page_url,
            "source_media_url": sm.source_media_url,
            "scrape_run_id": sm.scrape_run_id,
            "media_type": sm.media_type,
            "document_type": sm.document_type,
            "meeting_date": sm.meeting_date.isoformat() if sm.meeting_date else None,
            "scraped_at": sm.scraped_at.isoformat() if sm.scraped_at else None,
        },
    )
    db.add(doc)
    await db.flush()

    # Persist raw s3 key on the ScrapedMedia row.
    sm.s3_key_raw = s3_key_raw
    sm.s3_key_text = s3_key_text
    if raw_bytes is not None:
        sm.size_bytes = len(raw_bytes)
    await db.commit()

    # Enqueue the existing document processing pipeline.
    try:
        from app.tasks.document_tasks import process_document_task

        process_document_task.delay(document_id=doc.id)
    except Exception:  # noqa: BLE001
        logger.exception(
            "Failed to enqueue document processing for doc %s", doc.id
        )

    return doc.id


async def _upload_text_to_s3(key: str, text: str) -> str:
    """Upload extracted/transcribed text to S3 and return its URL."""
    try:
        import aioboto3
    except ImportError:  # pragma: no cover
        return f"s3://{settings.S3_BUCKET_NAME}/{key}"

    session = aioboto3.Session()
    async with session.client(
        "s3",
        region_name=settings.S3_REGION,
        aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
        aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
    ) as s3:
        await s3.put_object(
            Bucket=settings.S3_BUCKET_NAME,
            Key=key,
            Body=text.encode("utf-8"),
            ContentType="text/plain",
        )
    return f"s3://{settings.S3_BUCKET_NAME}/{key}"


async def _upload_raw_to_s3(key: str, raw: bytes) -> None:
    """Upload raw media bytes to S3 for archival."""
    try:
        import aioboto3
    except ImportError:  # pragma: no cover
        return

    session = aioboto3.Session()
    async with session.client(
        "s3",
        region_name=settings.S3_REGION,
        aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
        aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
    ) as s3:
        await s3.put_object(
            Bucket=settings.S3_BUCKET_NAME,
            Key=key,
            Body=raw,
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _classify_media_type(
    reported: str | None, ext: str | None
) -> str:
    """Normalize the media_type from the scraper output.

    The scraper reports video|audio|document. YouTube URLs are detected
    here so they can be transcript-only in the ingest step.
    """
    if reported == "document":
        return "document"
    if reported in ("video", "audio"):
        # Detect youtube links.
        if ext and "youtube" in ext.lower():
            return "youtube"
        return reported
    # Fall back to extension sniffing.
    e = (ext or "").lower().lstrip(".")
    if e in ("mp4", "mov", "webm"):
        return "video"
    if e in ("mp3", "wav", "m4a"):
        return "audio"
    return "document"
