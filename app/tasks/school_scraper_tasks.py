"""
Celery task for ingesting scraped media into the document pipeline.

Scrape orchestration (discovery, run/job tracking) is handled offline via
scripts; this module only covers download/transcribe → Document → vectors.
"""

import logging
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.celery_app import celery_app
from app.core.config import settings
from app.db.connector import AsyncSessionLocal
from app.models.school import ScrapedMedia
from app.tasks.loop_utils import get_event_loop

logger = logging.getLogger(__name__)


@celery_app.task(
    name="app.tasks.school_scraper_tasks.ingest_scraped_media",
    bind=True,
    max_retries=2,
)
def ingest_scraped_media(self, scraped_media_id: int):
    """Download/transcribe a scraped media item and ingest into the doc pipeline."""
    loop = get_event_loop()
    return loop.run_until_complete(_ingest_scraped_media_async(scraped_media_id))


async def _ingest_scraped_media_async(scraped_media_id: int) -> dict:
    from app.crud.schools import (
        get_scraped_media_by_content_hash,
        update_scraped_media,
    )
    from app.services.web_scraper.year_filter import evaluate_media_year

    async with AsyncSessionLocal() as db:
        sm = await db.get(ScrapedMedia, scraped_media_id)
        if not sm:
            return {"scraped_media_id": scraped_media_id, "error": "not found"}

        inferred_year, should_process, skip_reason = evaluate_media_year(
            url=sm.source_media_url,
            filename=sm.original_name,
            source_page_url=sm.source_page_url,
        )
        if not should_process:
            await update_scraped_media(
                db,
                sm.id,
                status="skipped_year",
                doc_year=inferred_year,
                error_message=skip_reason,
            )
            return {
                "scraped_media_id": scraped_media_id,
                "status": "skipped_year",
                "doc_year": inferred_year,
            }

        await update_scraped_media(db, sm.id, status="downloading")

        try:
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

        if inferred_year is not None:
            sm.doc_year = inferred_year
            await db.flush()

        if content_h:
            existing = await get_scraped_media_by_content_hash(db, sm.school_id, content_h)
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
    """Return (raw_bytes, text_content, content_hash) for a ScrapedMedia item."""
    import hashlib

    import httpx

    if sm.media_type == "youtube":
        if not settings.SCHOOL_SCRAPER_YOUTUBE_TRANSCRIPT_ENABLED:
            raise RuntimeError(
                "YouTube transcript disabled (SCHOOL_SCRAPER_YOUTUBE_TRANSCRIPT_ENABLED=False)"
            )
        text = await _fetch_youtube_transcript(sm.source_media_url)
        return None, text, None

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
    else:
        if not settings.SCHOOL_SCRAPER_WHISPER_TRANSCRIPTION_ENABLED:
            text = ""
        else:
            text = await _transcribe_media(raw, sm.file_extension)

    return raw, text, content_h


async def _fetch_youtube_transcript(url: str) -> str:
    try:
        from yt_dlp import YoutubeDL  # type: ignore
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError(
            "yt-dlp is not installed; cannot fetch YouTube transcript"
        ) from exc

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
            return str(track[0].get("ext", ""))

    return await asyncio.to_thread(_fetch)


def _extract_text_from_document(raw: bytes, ext: str | None) -> str:
    import io
    import os
    import tempfile

    ext = (ext or "").lower().lstrip(".")
    if ext == "pdf":
        import fitz  # pymupdf

        with fitz.open(stream=raw, filetype="pdf") as doc:
            return "\n".join(page.get_text() for page in doc)
    if ext == "docx":
        import docx

        d = docx.Document(io.BytesIO(raw))
        return "\n".join(p.text for p in d.paragraphs)
    if ext == "pptx":
        from pptx import Presentation

        presentation = Presentation(io.BytesIO(raw))
        parts: list[str] = []
        for slide in presentation.slides:
            for shape in slide.shapes:
                if shape.has_text_frame:
                    for paragraph in shape.text_frame.paragraphs:
                        run_text = "".join(run.text for run in paragraph.runs)
                        if run_text or paragraph.text:
                            parts.append(run_text or paragraph.text)
                if getattr(shape, "has_table", False):
                    for row in shape.table.rows:
                        for cell in row.cells:
                            if cell.text:
                                parts.append(cell.text)
        return "\n".join(parts)
    if ext in ("doc", "xlsx", "xls", "txt", "text", "md"):
        suffix = f".{ext}"
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp.write(raw)
            tmp_path = tmp.name
        try:
            from app.services.document_processing.factory import ProcessorFactory

            processor = ProcessorFactory.get_processor(tmp_path)
            return processor.extract_text(tmp_path)
        finally:
            os.unlink(tmp_path)
    try:
        return raw.decode("utf-8", errors="ignore")
    except Exception:  # noqa: BLE001
        return ""


async def _transcribe_media(raw: bytes, ext: str | None) -> str:
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
    from app.models.documents import Document, ProcessingStatus
    from app.models.school import School
    from app.utils.school_calendar import (
        derive_quarter_month,
        derive_school_year,
    )

    s3_key_text = (
        f"tenants/{sm.tenant_id}/schools/{sm.school_org_code}/"
        f"{sm.media_type}/{content_h or sm.url_hash}/transcript.txt"
    )
    s3_url_text = await _upload_text_to_s3(s3_key_text, text_content)

    s3_key_raw = None
    file_ext = (sm.file_extension or "bin").lstrip(".").lower()
    if raw_bytes is not None:
        s3_key_raw = (
            f"tenants/{sm.tenant_id}/schools/{sm.school_org_code}/"
            f"{sm.media_type}/{content_h}/{sm.original_name or f'file.{file_ext}'}"
        )
        await _upload_raw_to_s3(s3_key_raw, raw_bytes)

    # Prefer the raw binary for document types so the pipeline (including OCR)
    # can process the real file. Audio/video stay on transcript.txt.
    _RAW_DOC_EXTS = {"pdf", "docx", "doc", "pptx", "xlsx", "xls"}
    use_raw_document = (
        sm.media_type == "document"
        and s3_key_raw is not None
        and file_ext in _RAW_DOC_EXTS
    )
    if use_raw_document:
        doc_s3_url = f"s3://{settings.S3_BUCKET_NAME}/{s3_key_raw}"
        doc_type = f".{file_ext}"
    else:
        doc_s3_url = s3_url_text
        doc_type = ".txt"

    # Resolve the school's state (2-letter abbreviation) so it can be
    # denormalized onto the Document row + source_metadata. Falls back to
    # 'MA' if the school row is missing (V1 corpus is MA-only).
    school = await db.get(School, sm.school_id)
    state = (school.state if school else None) or "MA"

    # Derive school_year + quarter_month from the scraped meeting_date if
    # present. DocClassifier (step 2.6) may overwrite meeting_date from the
    # LLM and re-derive these; setting them now means non-LLM paths still
    # have correct values.
    school_year: str | None = None
    quarter_month: str | None = None
    if sm.meeting_date:
        school_year = derive_school_year(sm.meeting_date)
        quarter_month = derive_quarter_month(sm.meeting_date)

    doc = Document(
        name=sm.original_name or sm.source_media_url,
        doc_id=f"school-{sm.school_org_code}-{content_h or sm.url_hash[:16]}",
        s3_url=doc_s3_url,
        tenant_id=sm.tenant_id,
        document_type=doc_type,
        processing_status=ProcessingStatus.PENDING,
        source_type="school_scraper",
        content_hash=content_h,
        # Heatmap V1 doc-level denorm (spec: Heatmap Ingest Metadata v1).
        state=state,
        district_name=sm.school_name,
        school_year=school_year,
        quarter_month=quarter_month,
        meeting_date=sm.meeting_date,
        source_metadata={
            "scraped_media_id": sm.id,
            "school_id": sm.school_id,
            "school_org_code": sm.school_org_code,
            "school_name": sm.school_name,
            "district_type": sm.district_type,
            "state": state,
            "source_page_url": sm.source_page_url,
            "source_media_url": sm.source_media_url,
            "media_type": sm.media_type,
            "document_type": sm.document_type,
            "meeting_date": sm.meeting_date.isoformat() if sm.meeting_date else None,
            "school_year": school_year,
            "quarter_month": quarter_month,
            "doc_year": sm.doc_year,
            "scraped_at": sm.scraped_at.isoformat() if sm.scraped_at else None,
        },
    )
    db.add(doc)
    await db.flush()

    sm.s3_key_raw = s3_key_raw
    sm.s3_key_text = s3_key_text
    if raw_bytes is not None:
        sm.size_bytes = len(raw_bytes)

    from app.models.processing_jobs import DocumentProcessingJob, JobStatus

    job = DocumentProcessingJob(
        document_id=doc.id,
        status=JobStatus.PENDING,
        processor_type=doc.document_type,
    )
    db.add(job)
    await db.commit()

    try:
        from app.tasks.document_pipeline import process_document_pipeline

        process_document_pipeline.delay(doc.id, job.id)
    except Exception:  # noqa: BLE001
        logger.exception(
            "Failed to enqueue document processing for doc %s", doc.id
        )

    return doc.id


async def _upload_text_to_s3(key: str, text: str) -> str:
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
