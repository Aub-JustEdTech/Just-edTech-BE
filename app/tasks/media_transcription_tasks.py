"""Transcription stage for tenant-uploaded media and pasted links.

Runs BEFORE the document pipeline rather than inside it. The pipeline's first
act is to download a file from S3 and hand it to a processor keyed on its
extension — there is no processor for ``.mp4``, and there cannot be one, because
turning media into text is a paid network round trip that takes minutes, not a
local parse.

So this task does what the school scraper already does, just sourced from an
upload instead of a crawl: transcribe, write both transcript artifacts to S3,
repoint the Document at the envelope, then hand off to the existing pipeline
unchanged.

Ordering of the spend guards, cheapest first:

    quota check (DB) -> media gates (one ffprobe header read) -> transcribe
         free                      ~1.5s, no download            paid
"""

from __future__ import annotations

import logging
import shutil
import traceback
from pathlib import Path
from typing import Any

from app.celery_app import celery_app
from app.core.config import settings
from app.db.connector import AsyncSessionLocal
from app.models.documents import Document, ProcessingStatus
from app.models.processing_jobs import JobStatus
from app.models.processing_stages import ProcessingStage, StageStatus
from app.services.media_ingest_service import (
    SOURCE_TYPE_MEDIA_LINK,
    media_ingest_service,
)
from app.services.media_usage_service import (
    MediaQuotaExceededError,
    media_usage_service,
)
from app.services.transcription.exceptions import (
    TerminalTranscriptionError,
    TranscriptionError,
)
from app.services.transcription.schemas import SOURCE_YOUTUBE_CAPTIONS
from app.tasks.document_pipeline import (
    _create_stage_record,
    _update_document_status,
    _update_job_status,
    _update_stage_status,
)
from app.tasks.loop_utils import get_event_loop
from app.utils.redis_pipeline import get_redis_tracker

logger = logging.getLogger(__name__)

STAGE_NAME = "transcribing"


@celery_app.task(name="pipeline.transcribe_media", bind=True, max_retries=2)
def transcribe_media_task(
    self, document_id: int, job_id: int, batch_id: int | None = None
) -> dict[str, Any]:
    """Transcribe one media Document, then start the normal document pipeline."""
    loop = get_event_loop()
    redis_tracker = get_redis_tracker()

    try:
        logger.info(f"[Doc {document_id}] Transcription stage starting")
        redis_tracker.set_document_status(
            doc_id=document_id,
            stage=STAGE_NAME,
            status="in_progress",
            progress=5.0,
        )
        redis_tracker.update_stage(document_id, STAGE_NAME, "in_progress")

        result = loop.run_until_complete(_transcribe_async(document_id, job_id))

        redis_tracker.update_stage(document_id, STAGE_NAME, "completed")
        redis_tracker.set_document_status(
            doc_id=document_id,
            stage=STAGE_NAME,
            status="completed",
            progress=20.0,
            metadata={"duration_seconds": result.get("duration_seconds") or 0},
        )

        # Hand off to the unmodified document pipeline. Imported here rather
        # than at module scope: document_pipeline imports are already loaded,
        # but the task reference must resolve after registration.
        from app.tasks.document_pipeline import process_document_pipeline

        process_document_pipeline.delay(document_id, job_id, batch_id)
        logger.info(f"[Doc {document_id}] Transcribed; document pipeline queued")
        return result

    except (TerminalTranscriptionError, MediaQuotaExceededError) as exc:
        # Terminal by definition — no captions, no audio, over the cap, out of
        # budget. Retrying spends the same money (or hits the same wall) for
        # the same answer.
        logger.warning(f"[Doc {document_id}] Transcription rejected: {exc}")
        loop.run_until_complete(_fail(document_id, job_id, str(exc)))
        redis_tracker.update_stage(document_id, STAGE_NAME, "failed")
        redis_tracker.set_document_status(
            doc_id=document_id, stage=STAGE_NAME, status="failed", progress=0.0
        )
        return {"document_id": document_id, "status": "failed", "error": str(exc)}

    except TranscriptionError as exc:
        # Transient: provider timeout, network. Worth another attempt.
        logger.error(f"[Doc {document_id}] Transcription failed: {exc}")
        redis_tracker.update_stage(document_id, STAGE_NAME, "retrying")
        try:
            raise self.retry(exc=exc, countdown=120) from exc
        except self.MaxRetriesExceededError:
            loop.run_until_complete(_fail(document_id, job_id, str(exc)))
            redis_tracker.update_stage(document_id, STAGE_NAME, "failed")
            return {"document_id": document_id, "status": "failed", "error": str(exc)}

    except Exception as exc:
        logger.error(
            f"[Doc {document_id}] Transcription stage crashed: {exc}\n"
            f"{traceback.format_exc()}"
        )
        loop.run_until_complete(_fail(document_id, job_id, str(exc)))
        redis_tracker.update_stage(document_id, STAGE_NAME, "failed")
        redis_tracker.set_document_status(
            doc_id=document_id, stage=STAGE_NAME, status="failed", progress=0.0
        )
        raise


async def _transcribe_async(document_id: int, job_id: int) -> dict[str, Any]:
    from app.services.transcription.service import transcription_service

    workdir = Path(settings.MEDIA_INGEST_TEMP_DIR) / str(document_id)
    workdir.mkdir(parents=True, exist_ok=True)

    async with AsyncSessionLocal() as db:
        stage_record = await _create_stage_record(
            db, document_id, job_id, ProcessingStage.TRANSCRIBING
        )
        await _update_stage_status(db, stage_record.id, StageStatus.IN_PROGRESS)
        await _update_document_status(db, document_id, ProcessingStatus.PROCESSING)
        await _update_job_status(db, job_id, JobStatus.PROCESSING)

        document = await db.get(Document, document_id)
        if not document:
            raise ValueError(f"Document {document_id} not found")

        tenant_id = document.tenant_id
        meta = document.source_metadata or {}
        is_youtube = meta.get("media_type") == "youtube"

        try:
            # --- Guard 1: is this tenant already out of budget? Free. ---
            # YouTube is checked too but usually passes: captions cost nothing
            # and are recorded as non-billable, so they never consume quota.
            if not is_youtube:
                await media_usage_service.assert_within_quota(db, tenant_id)

            source_url = await media_ingest_service.resolve_media_source(document)

            # --- Guard 2: probe the media, then re-check quota against its
            # real duration. Splitting the quota check in two is what stops a
            # single three-hour upload from blowing a nearly-full budget. ---
            if is_youtube:
                transcript = await transcription_service.transcribe_youtube(
                    source_url, workdir=workdir
                )
            else:
                probe = await transcription_service.enforce_media_gates(source_url)
                if probe.duration_seconds:
                    await media_usage_service.assert_within_quota(
                        db, tenant_id, additional_seconds=probe.duration_seconds
                    )
                transcript = await transcription_service.transcribe_media_url(
                    source_url, workdir=workdir
                )

            if transcript.is_empty:
                raise TerminalTranscriptionError(
                    f"Transcription of document {document_id} produced no text"
                )

            # Record the spend BEFORE storing the transcript. The money is
            # already gone the moment the provider returned, so the usage row
            # is true from here on. Storing first would mean an S3 or DB
            # failure during the write loses the record of a charge that
            # really happened — and the retry would pay for it a second time.
            billable = transcript.source != SOURCE_YOUTUBE_CAPTIONS  # captions are free
            await media_usage_service.record_usage(
                db,
                tenant_id=tenant_id,
                document_id=document_id,
                source=meta.get("media_type")
                if document.source_type == SOURCE_TYPE_MEDIA_LINK
                else "upload",
                duration_seconds=transcript.duration_seconds,
                billable=billable,
                provider=transcript.source,
                speech_model=transcript.speech_model,
            )
            await db.commit()

            await media_ingest_service.store_transcript(db, document, transcript)

            await _update_stage_status(
                db,
                stage_record.id,
                StageStatus.COMPLETED,
                output_size=len(transcript.segments),
            )

            return {
                "document_id": document_id,
                "status": "completed",
                "segments": len(transcript.segments),
                "duration_seconds": transcript.duration_seconds,
                "transcript_source": transcript.source,
                "billable": billable,
            }

        except Exception as exc:
            # Roll back before writing the failure. If the exception came from
            # the database itself the transaction is already aborted, and every
            # further statement on this session — including this very status
            # write — would fail too, losing the error message that explains
            # what went wrong.
            try:
                await db.rollback()
            except Exception:  # noqa: BLE001
                logger.exception(
                    "[Doc %s] Rollback failed while handling a "
                    "transcription error",
                    document_id,
                )
            await _update_stage_status(
                db,
                stage_record.id,
                StageStatus.FAILED,
                error_message=str(exc),
                error_traceback=traceback.format_exc(),
            )
            raise
        finally:
            shutil.rmtree(workdir, ignore_errors=True)


async def _fail(document_id: int, job_id: int, error: str) -> None:
    async with AsyncSessionLocal() as db:
        await _update_document_status(
            db, document_id, ProcessingStatus.FAILED, error_message=error
        )
        await _update_job_status(db, job_id, JobStatus.FAILED)
