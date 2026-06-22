"""
Document Processing Pipeline with Celery Canvas
Breaks document processing into 5 individual step tasks for fine-grained tracking.

Pipeline stages:
1. Download from S3
2. Extract text and metadata
3. Chunk text
4. Generate embeddings
5. Store in vector database

Each stage:
- Tracks its own status in Redis (fast) and PostgreSQL (durable)
- Can be retried independently
- Reports progress in real-time
- Logs errors with full context
"""

import logging
import os
import traceback
from datetime import datetime
from typing import Any

from celery import chain
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.celery_app import celery_app
from app.core.config import settings
from app.db.connector import AsyncSessionLocal
from app.models.documents import Document, ProcessingStatus
from app.models.processing_jobs import DocumentProcessingJob, JobStatus
from app.models.processing_stages import (
    DocumentProcessingStage,
    ProcessingStage,
    StageStatus,
)
from app.services.chatbot_config_service import chatbot_config_service
from app.services.document_processing.chunker import Chunker
from app.services.document_processing.factory import ProcessorFactory
from app.services.embeddings.embedding_service import EmbeddingService
from app.services.image_caption_service import ImageCaptionService
from app.services.vector_store.factory import VectorStoreFactory, VectorStoreType
from app.tasks.loop_utils import get_event_loop
from app.utils.redis_pipeline import get_redis_tracker
from app.utils.s3 import S3Manager

logger = logging.getLogger(__name__)


# ==================== Helper Functions ====================


async def _create_stage_record(
    db: AsyncSession,
    document_id: int,
    job_id: int,
    stage: ProcessingStage,
) -> DocumentProcessingStage:
    """Create a stage record in PostgreSQL."""
    stage_record = DocumentProcessingStage(
        document_id=document_id,
        job_id=job_id,
        stage=stage,
        status=StageStatus.PENDING,
    )
    db.add(stage_record)
    await db.commit()
    await db.refresh(stage_record)
    return stage_record


async def _update_stage_status(
    db: AsyncSession,
    stage_id: int,
    status: StageStatus,
    error_message: str | None = None,
    error_traceback: str | None = None,
    input_size: int | None = None,
    output_size: int | None = None,
) -> None:
    """Update stage status in PostgreSQL."""
    stage_record = await db.get(DocumentProcessingStage, stage_id)
    if not stage_record:
        return

    stage_record.status = status

    if status == StageStatus.IN_PROGRESS:
        stage_record.started_at = datetime.utcnow()
    elif status in [StageStatus.COMPLETED, StageStatus.FAILED]:
        stage_record.completed_at = datetime.utcnow()
        if stage_record.started_at:
            stage_record.duration_seconds = (
                stage_record.completed_at - stage_record.started_at
            ).total_seconds()

    if error_message:
        stage_record.error_message = error_message
    if error_traceback:
        stage_record.error_traceback = error_traceback
    if input_size is not None:
        stage_record.input_size = input_size
    if output_size is not None:
        stage_record.output_size = output_size

    await db.commit()


async def _update_document_status(
    db: AsyncSession,
    document_id: int,
    status: ProcessingStatus,
    error_message: str | None = None,
) -> None:
    """Update document processing status and optional error message."""
    document = await db.get(Document, document_id)
    if document:
        document.processing_status = status
        if error_message is not None:
            document.error_message = error_message
        await db.commit()


async def _update_job_status(db: AsyncSession, job_id: int, status: JobStatus) -> None:
    """Update job status."""
    job = await db.get(DocumentProcessingJob, job_id)
    if job:
        job.status = status
        await db.commit()


# ==================== Pipeline Context ====================


class PipelineContext:
    """
    Context object passed between pipeline stages.
    Contains all data needed for processing.
    """

    def __init__(self, document_id: int, job_id: int, batch_id: int | None = None):
        self.document_id = document_id
        self.job_id = job_id
        self.batch_id = batch_id
        self.tenant_id: int | None = None
        self.doc_uuid: str | None = None
        self.document_type: str | None = None
        self.s3_url: str | None = None
        self.temp_file_path: str | None = None
        self.extracted_text: str | None = None
        self.doc_metadata: dict[str, Any] = {}
        self.chunks: list[str] = []
        self.embeddings: list[list[float]] = []
        self.chunk_metadatas: list[dict[str, Any]] = []
        self.stage_ids: dict[str, int] = {}  # Map stage name to stage_id

    def to_dict(self) -> dict[str, Any]:
        """Convert context to dictionary for passing between tasks."""
        return {
            "document_id": self.document_id,
            "job_id": self.job_id,
            "batch_id": self.batch_id,
            "tenant_id": self.tenant_id,
            "doc_uuid": self.doc_uuid,
            "document_type": self.document_type,
            "s3_url": self.s3_url,
            "temp_file_path": self.temp_file_path,
            "extracted_text": self.extracted_text,
            "doc_metadata": self.doc_metadata,
            "chunks": self.chunks,
            "embeddings": self.embeddings,
            "chunk_metadatas": self.chunk_metadatas,
            "stage_ids": self.stage_ids,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PipelineContext":
        """Create context from dictionary."""
        ctx = cls(
            document_id=data["document_id"],
            job_id=data["job_id"],
            batch_id=data.get("batch_id"),
        )
        ctx.tenant_id = data.get("tenant_id")
        ctx.doc_uuid = data.get("doc_uuid")
        ctx.document_type = data.get("document_type")
        ctx.s3_url = data.get("s3_url")
        ctx.temp_file_path = data.get("temp_file_path")
        ctx.extracted_text = data.get("extracted_text")
        ctx.doc_metadata = data.get("doc_metadata", {})
        ctx.chunks = data.get("chunks", [])
        ctx.embeddings = data.get("embeddings", [])
        ctx.chunk_metadatas = data.get("chunk_metadatas", [])
        ctx.stage_ids = data.get("stage_ids", {})
        return ctx


# ==================== Stage 1: Download from S3 ====================


@celery_app.task(name="pipeline.download_from_s3", bind=True, max_retries=3)
def step1_download_from_s3(self, context_dict: dict[str, Any]) -> dict[str, Any]:
    """
    Stage 1: Download document from S3 to temporary file.

    Updates:
    - Redis: stage="downloading", status="in_progress"
    - PostgreSQL: DocumentProcessingStage record
    - Document: processing_status="processing"

    Returns:
        Updated context dictionary with temp_file_path
    """
    ctx = PipelineContext.from_dict(context_dict)
    redis_tracker = get_redis_tracker()
    loop = get_event_loop()

    try:
        logger.info(f"[Doc {ctx.document_id}] Stage 1: Downloading from S3")

        # Update Redis status
        redis_tracker.set_document_status(
            ctx.document_id,
            stage="downloading",
            status="in_progress",
            progress=0.0,
        )
        redis_tracker.update_stage(ctx.document_id, "downloading", "in_progress")
        redis_tracker.add_active_job(ctx.document_id)

        # Run async operations
        loop.run_until_complete(_step1_download_async(ctx, redis_tracker))

        logger.info(
            f"[Doc {ctx.document_id}] Stage 1 completed: Downloaded to {ctx.temp_file_path}"
        )

        # Update Redis status
        redis_tracker.update_stage(ctx.document_id, "downloading", "completed")
        redis_tracker.set_document_status(
            ctx.document_id,
            stage="downloading",
            status="completed",
            progress=20.0,
        )

        return ctx.to_dict()

    except Exception as exc:
        logger.error(f"[Doc {ctx.document_id}] Stage 1 failed: {exc}", exc_info=True)

        # Update Redis with error
        redis_tracker.update_stage(ctx.document_id, "downloading", "failed")
        redis_tracker.log_error(
            ctx.document_id,
            stage="downloading",
            error=str(exc),
            retry_count=self.request.retries,
        )

        # Update PostgreSQL
        loop.run_until_complete(_mark_stage_failed(ctx, "downloading", str(exc)))

        # Retry with exponential backoff
        raise self.retry(exc=exc, countdown=60 * (2**self.request.retries)) from exc


async def _step1_download_async(ctx: PipelineContext, redis_tracker):
    """Async implementation of S3 download."""
    async with AsyncSessionLocal() as db:
        # Create stage record
        stage_record = await _create_stage_record(
            db, ctx.document_id, ctx.job_id, ProcessingStage.DOWNLOADING
        )
        ctx.stage_ids["downloading"] = stage_record.id

        # Update stage to in_progress
        await _update_stage_status(db, stage_record.id, StageStatus.IN_PROGRESS)

        # Update document status
        await _update_document_status(db, ctx.document_id, ProcessingStatus.PROCESSING)
        await _update_job_status(db, ctx.job_id, JobStatus.PROCESSING)

        # Get document details
        document = await db.get(Document, ctx.document_id)
        if not document:
            raise ValueError(f"Document {ctx.document_id} not found")

        ctx.tenant_id = document.tenant_id
        ctx.doc_uuid = document.doc_id
        ctx.document_type = document.document_type
        ctx.s3_url = document.s3_url

        # Download from S3
        s3_manager = S3Manager(
            bucket_name=settings.S3_BUCKET_NAME,
            region_name=settings.S3_REGION,
            aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
            aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
        )

        ctx.temp_file_path = os.path.join(
            settings.TEMP_UPLOAD_DIR, f"{ctx.doc_uuid}{ctx.document_type}"
        )

        s3_key = ctx.s3_url.split(f"{settings.S3_BUCKET_NAME}/")[1]
        await s3_manager.download_file_object(s3_key, ctx.temp_file_path)

        # Get file size
        file_size = os.path.getsize(ctx.temp_file_path)

        # Update stage to completed
        await _update_stage_status(
            db,
            stage_record.id,
            StageStatus.COMPLETED,
            input_size=file_size,
            output_size=file_size,
        )


# ==================== Stage 2: Extract Text ====================


@celery_app.task(name="pipeline.extract_text", bind=True, max_retries=3)
def step2_extract_text(self, context_dict: dict[str, Any]) -> dict[str, Any]:
    """
    Stage 2: Extract text and metadata from document.

    Updates:
    - Redis: stage="extracting", status="in_progress"
    - PostgreSQL: DocumentProcessingStage record

    Returns:
        Updated context dictionary with extracted_text and doc_metadata
    """
    ctx = PipelineContext.from_dict(context_dict)
    redis_tracker = get_redis_tracker()
    loop = get_event_loop()

    try:
        logger.info(f"[Doc {ctx.document_id}] Stage 2: Extracting text")

        # Update Redis status
        redis_tracker.set_document_status(
            ctx.document_id,
            stage="extracting",
            status="in_progress",
            progress=20.0,
        )
        redis_tracker.update_stage(ctx.document_id, "extracting", "in_progress")

        # Run async operations
        loop.run_until_complete(_step2_extract_async(ctx, redis_tracker))

        logger.info(
            f"[Doc {ctx.document_id}] Stage 2 completed: Extracted {len(ctx.extracted_text)} characters"
        )

        # Update Redis status
        redis_tracker.update_stage(ctx.document_id, "extracting", "completed")
        redis_tracker.set_document_status(
            ctx.document_id,
            stage="extracting",
            status="completed",
            progress=40.0,
            metadata={"text_length": len(ctx.extracted_text)},
        )

        return ctx.to_dict()

    except Exception as exc:
        logger.error(f"[Doc {ctx.document_id}] Stage 2 failed: {exc}", exc_info=True)

        # Update Redis with error
        redis_tracker.update_stage(ctx.document_id, "extracting", "failed")
        redis_tracker.log_error(
            ctx.document_id,
            stage="extracting",
            error=str(exc),
            retry_count=self.request.retries,
        )

        # Update PostgreSQL
        loop.run_until_complete(_mark_stage_failed(ctx, "extracting", str(exc)))

        # For non-recoverable errors like "no text extracted", do not keep retrying.
        # Re-raise the original exception so the task is marked as failed immediately.
        if isinstance(exc, ValueError) and str(exc) == "No text extracted from document":
            if ctx.temp_file_path and os.path.exists(ctx.temp_file_path):
                logger.info(
                    f"[Doc {ctx.document_id}] Cleaning up temp file after unrecoverable error"
                )
                os.remove(ctx.temp_file_path)
            raise

        # Only cleanup temp file on final retry failure
        if self.request.retries >= self.max_retries:
            if ctx.temp_file_path and os.path.exists(ctx.temp_file_path):
                logger.info(
                    f"[Doc {ctx.document_id}] Cleaning up temp file after final retry"
                )
                os.remove(ctx.temp_file_path)

        # Retry
        raise self.retry(exc=exc, countdown=60 * (2**self.request.retries)) from exc


from app.core.config import settings


async def _step2_extract_async(ctx: PipelineContext, redis_tracker):
    """Async implementation of text extraction."""
    async with AsyncSessionLocal() as db:
        # Create stage record
        stage_record = await _create_stage_record(
            db, ctx.document_id, ctx.job_id, ProcessingStage.EXTRACTING
        )
        ctx.stage_ids["extracting"] = stage_record.id

        # Update stage to in_progress
        await _update_stage_status(db, stage_record.id, StageStatus.IN_PROGRESS)

        # Extract text using appropriate processor
        processor = ProcessorFactory.get_processor(ctx.temp_file_path)
        ctx.doc_metadata = processor.extract_metadata(ctx.temp_file_path)

        # For PDFs, preserve per-page text so chunking can attach page_number metadata.
        if ctx.document_type == ".pdf" and hasattr(processor, "extract_text_by_page"):
            pages_text = processor.extract_text_by_page(ctx.temp_file_path)
            ctx.doc_metadata["_pdf_pages_text"] = pages_text
            # Keep extracted_text as a full string for summarization / logging.
            ctx.extracted_text = "\n\n".join([p for p in pages_text if p])
        else:
            ctx.extracted_text = processor.extract_text(ctx.temp_file_path)

        if not ctx.extracted_text or len(ctx.extracted_text.strip()) == 0:
            raise ValueError("No text extracted from document")

        # For spreadsheets, pre-compute table-aware chunks (headers repeated per chunk).
        # Stored in doc_metadata so step3 can use them without re-opening the file.
        if ctx.document_type in (".xlsx", ".xls") and hasattr(
            processor, "chunk_spreadsheet"
        ):
            spreadsheet_chunks = processor.chunk_spreadsheet(ctx.temp_file_path)
            ctx.doc_metadata["_xlsx_pre_chunks"] = [c["text"] for c in spreadsheet_chunks]
            ctx.doc_metadata["_xlsx_chunk_meta"] = [
                {
                    "sheet_name": c["sheet_name"],
                    "row_start": c["row_start"],
                    "row_end": c["row_end"],
                }
                for c in spreadsheet_chunks
            ]
            logger.info(
                f"[Doc {ctx.document_id}] Pre-computed {len(spreadsheet_chunks)} "
                "spreadsheet chunks"
            )

        # Extract images if enabled and PDF processor supports it
        extracted_images = []
        if (
            ctx.document_type == ".pdf"
            and getattr(settings, "ENABLE_IMAGE_EXTRACTION", True)
            and hasattr(processor, "extract_images")
        ):
            try:
                # Get document to access name
                document = await db.get(Document, ctx.document_id)
                if document:
                    # Use document name (without extension) for image filenames
                    pdf_name = os.path.splitext(document.name)[0]
                else:
                    # Fallback to doc_uuid or filename
                    pdf_name = ctx.doc_uuid or os.path.splitext(
                        os.path.basename(ctx.temp_file_path)
                    )[0]
                
                # Extract images with surrounding text context
                if hasattr(processor, "extract_images_with_context"):
                    extracted_images = processor.extract_images_with_context(
                        ctx.temp_file_path, pdf_name, context_chars=500
                    )
                else:
                    extracted_images = processor.extract_images(ctx.temp_file_path, pdf_name)
                logger.info(
                    f"[Doc {ctx.document_id}] Extracted {len(extracted_images)} images"
                )
                # Store image metadata in doc_metadata
                if "extracted_images" not in ctx.doc_metadata:
                    ctx.doc_metadata["extracted_images"] = []
                ctx.doc_metadata["extracted_images"] = extracted_images
            except Exception as e:
                logger.warning(
                    f"[Doc {ctx.document_id}] Image extraction failed: {e}",
                    exc_info=True,
                )
                # Don't fail the entire extraction if image extraction fails

        # Update stage to completed
        await _update_stage_status(
            db,
            stage_record.id,
            StageStatus.COMPLETED,
            input_size=os.path.getsize(ctx.temp_file_path),
            output_size=len(ctx.extracted_text),
        )


# ==================== Stage 2.5: Summarise Document ====================


@celery_app.task(name="pipeline.summarize_document", bind=True, max_retries=1)
def step2_5_summarize_document(self, context_dict: dict[str, Any]) -> dict[str, Any]:
    """
    Stage 2.5: Generate an LLM summary and index it in the summaries collection.

    This stage is intentionally lenient: a failure here logs a warning but does
    NOT abort the pipeline.  Document text extraction and chunking proceed
    regardless.

    Updates:
    - Redis: stage="summarizing", status="in_progress"
    - PostgreSQL: DocumentProcessingStage record
    - documents table: summary, doc_category, doc_date_range
    - Qdrant: tenant_{id}_summaries collection

    Returns:
        Updated context dictionary (unchanged on failure)
    """
    ctx = PipelineContext.from_dict(context_dict)
    redis_tracker = get_redis_tracker()
    loop = get_event_loop()

    try:
        logger.info(f"[Doc {ctx.document_id}] Stage 2.5: Summarising document")

        redis_tracker.set_document_status(
            ctx.document_id,
            stage="summarizing",
            status="in_progress",
            progress=35.0,
        )
        redis_tracker.update_stage(ctx.document_id, "summarizing", "in_progress")

        loop.run_until_complete(_step2_5_summarize_async(ctx, redis_tracker))

        redis_tracker.update_stage(ctx.document_id, "summarizing", "completed")
        redis_tracker.set_document_status(
            ctx.document_id,
            stage="summarizing",
            status="completed",
            progress=40.0,
        )

        logger.info(f"[Doc {ctx.document_id}] Stage 2.5 completed")

    except Exception as exc:
        # Non-fatal: log and continue the pipeline.
        logger.warning(
            f"[Doc {ctx.document_id}] Stage 2.5 (summarize) failed – "
            f"pipeline continues: {exc}",
            exc_info=True,
        )
        redis_tracker.update_stage(ctx.document_id, "summarizing", "failed")
        loop.run_until_complete(_mark_stage_failed(ctx, "summarizing", str(exc)))

    return ctx.to_dict()


async def _step2_5_summarize_async(ctx: PipelineContext, redis_tracker) -> None:
    """Async implementation of document summarisation."""
    from app.services.document_processing.summarizer import DocumentSummarizer

    async with AsyncSessionLocal() as db:
        stage_record = await _create_stage_record(
            db, ctx.document_id, ctx.job_id, ProcessingStage.SUMMARIZING
        )
        ctx.stage_ids["summarizing"] = stage_record.id
        await _update_stage_status(db, stage_record.id, StageStatus.IN_PROGRESS)

        document = await db.get(Document, ctx.document_id)
        doc_name = document.name if document else ""

        summarizer = DocumentSummarizer()
        await summarizer.summarize(
            text=ctx.extracted_text,
            document_id=ctx.document_id,
            doc_uuid=ctx.doc_uuid,
            document_name=doc_name,
            tenant_id=ctx.tenant_id,
            db=db,
        )

        await _update_stage_status(
            db,
            stage_record.id,
            StageStatus.COMPLETED,
            input_size=len(ctx.extracted_text),
        )


# ==================== Stage 2.6: Extract HeatMap Metadata ====================


@celery_app.task(name="pipeline.extract_heatmap_metadata", bind=True, max_retries=1)
def step2_6_extract_heatmap_metadata(self, context_dict: dict[str, Any]) -> dict[str, Any]:
    """
    Stage 2.6: Extract county/school_district/document_date from document text via LLM.

    Non-fatal: if extraction fails the pipeline continues unchanged.
    Extracted fields (county, school_district, document_date, state) are stored
    in ctx.doc_metadata so that Stage 5 automatically includes them on every chunk.
    """
    ctx = PipelineContext.from_dict(context_dict)
    loop = get_event_loop()

    try:
        logger.info(f"[Doc {ctx.document_id}] Stage 2.6: Extracting heatmap metadata")
        loop.run_until_complete(_step2_6_extract_async(ctx))
        logger.info(f"[Doc {ctx.document_id}] Stage 2.6 completed")
    except Exception as exc:
        logger.warning(
            f"[Doc {ctx.document_id}] Stage 2.6 (heatmap metadata) failed – "
            f"pipeline continues: {exc}",
            exc_info=True,
        )

    return ctx.to_dict()


async def _step2_6_extract_async(ctx: PipelineContext) -> None:
    """Async implementation of heatmap metadata extraction."""
    from openai import AsyncOpenAI

    from app.core.config import settings
    from app.crud.heatmap import heatmap_crud
    from app.db.connector import AsyncSessionLocal

    if not ctx.extracted_text:
        return

    client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)
    text_sample = ctx.extracted_text[:4000]

    from app.utils.geography import get_county_names
    valid_counties = get_county_names("MA")
    county_list = ", ".join(valid_counties)

    system_prompt = (
        "You are a document classification assistant. "
        "Given text from a Massachusetts school board document, extract the following as JSON:\n"
        '  "county"          – one of the valid MA county names listed below, or null if not determinable\n'
        '  "school_district" – the specific school district name, or null\n'
        '  "document_date"   – the meeting/publication date in YYYY-MM-DD format, or null\n\n'
        f"Valid MA county names: {county_list}.\n"
        "Return ONLY the JSON object. Do not wrap in markdown fences."
    )

    response = await client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"Document text:\n\n{text_sample}"},
        ],
        temperature=0,
        response_format={"type": "json_object"},
    )

    import json as _json

    raw = response.choices[0].message.content or "{}"
    extracted = _json.loads(raw)

    county = extracted.get("county")
    school_district = extracted.get("school_district")
    document_date = extracted.get("document_date")

    if county and county not in valid_counties:
        county = None

    if not county and school_district:
        async with AsyncSessionLocal() as db:
            resolved = await heatmap_crud.get_county_for_district(db, school_district)
            if resolved:
                county = resolved

    if county:
        ctx.doc_metadata["county"] = county
    if school_district:
        ctx.doc_metadata["school_district"] = school_district
    if document_date:
        ctx.doc_metadata["document_date"] = document_date
    ctx.doc_metadata["state"] = "MA"

    logger.info(
        f"[Doc {ctx.document_id}] HeatMap metadata: county={county}, "
        f"district={school_district}, date={document_date}"
    )


# ==================== Stage 3: Chunk Text ====================


@celery_app.task(name="pipeline.chunk_text", bind=True, max_retries=3)
def step3_chunk_text(self, context_dict: dict[str, Any]) -> dict[str, Any]:
    """
    Stage 3: Chunk text into smaller pieces.

    Updates:
    - Redis: stage="chunking", status="in_progress"
    - PostgreSQL: DocumentProcessingStage record

    Returns:
        Updated context dictionary with chunks
    """
    ctx = PipelineContext.from_dict(context_dict)
    redis_tracker = get_redis_tracker()
    loop = get_event_loop()

    try:
        logger.info(f"[Doc {ctx.document_id}] Stage 3: Chunking text")

        # Update Redis status
        redis_tracker.set_document_status(
            ctx.document_id,
            stage="chunking",
            status="in_progress",
            progress=40.0,
        )
        redis_tracker.update_stage(ctx.document_id, "chunking", "in_progress")

        # Run async operations
        loop.run_until_complete(_step3_chunk_async(ctx, redis_tracker))

        logger.info(
            f"[Doc {ctx.document_id}] Stage 3 completed: Created {len(ctx.chunks)} chunks"
        )

        # Update Redis status
        redis_tracker.update_stage(ctx.document_id, "chunking", "completed")
        redis_tracker.set_document_status(
            ctx.document_id,
            stage="chunking",
            status="completed",
            progress=60.0,
            metadata={"chunks_created": len(ctx.chunks)},
        )

        return ctx.to_dict()

    except Exception as exc:
        logger.error(f"[Doc {ctx.document_id}] Stage 3 failed: {exc}", exc_info=True)

        # Update Redis with error
        redis_tracker.update_stage(ctx.document_id, "chunking", "failed")
        redis_tracker.log_error(
            ctx.document_id,
            stage="chunking",
            error=str(exc),
            retry_count=self.request.retries,
        )

        # Update PostgreSQL
        loop.run_until_complete(_mark_stage_failed(ctx, "chunking", str(exc)))

        # Only cleanup temp file on final retry failure
        if self.request.retries >= self.max_retries:
            if ctx.temp_file_path and os.path.exists(ctx.temp_file_path):
                logger.info(
                    f"[Doc {ctx.document_id}] Cleaning up temp file after final retry"
                )
                os.remove(ctx.temp_file_path)

        # Retry
        raise self.retry(exc=exc, countdown=60 * (2**self.request.retries)) from exc


async def _step3_chunk_async(ctx: PipelineContext, redis_tracker):
    """Async implementation of text chunking."""
    async with AsyncSessionLocal() as db:
        # Create stage record
        stage_record = await _create_stage_record(
            db, ctx.document_id, ctx.job_id, ProcessingStage.CHUNKING
        )
        ctx.stage_ids["chunking"] = stage_record.id

        # Update stage to in_progress
        await _update_stage_status(db, stage_record.id, StageStatus.IN_PROGRESS)

        # Get default chatbot config for chunking settings
        chatbot_config_obj = await chatbot_config_service.get_default_chatbot_config(
            db, ctx.tenant_id
        )

        if not chatbot_config_obj:
            raise ValueError(f"Default chatbot config not found for tenant {ctx.tenant_id}")

        # Get chunking config from version history
        chunking_config = await chatbot_config_service.get_chunking_config(
            db, chatbot_config_obj.id
        )

        # For PDFs, chunk per page to preserve page_number metadata.
        if ctx.document_type == ".pdf" and "_pdf_pages_text" in ctx.doc_metadata:
            chunker = Chunker(
                chunk_size=chunking_config["chunk_size"],
                chunk_overlap=chunking_config["chunk_overlap"],
            )
            ctx.chunks = []
            ctx.chunk_metadatas = []

            pages_text = ctx.doc_metadata.get("_pdf_pages_text") or []
            for page_idx, page_text in enumerate(pages_text, start=1):
                if not page_text or not page_text.strip():
                    continue
                page_chunks = chunker.chunk_text(page_text)
                for ch in page_chunks:
                    ctx.chunks.append(ch)
                    ctx.chunk_metadatas.append({"page_number": page_idx})

            logger.info(
                f"[Doc {ctx.document_id}] Chunked PDF into {len(ctx.chunks)} chunks "
                f"across {len(pages_text)} pages"
            )
        # For spreadsheets use pre-computed table-aware chunks (headers repeated).
        # For all other document types use the standard text chunker.
        if "_xlsx_pre_chunks" in ctx.doc_metadata:
            ctx.chunks = ctx.doc_metadata.pop("_xlsx_pre_chunks")
            ctx.chunk_metadatas = ctx.doc_metadata.pop("_xlsx_chunk_meta", [])
            logger.info(
                f"[Doc {ctx.document_id}] Using {len(ctx.chunks)} pre-computed "
                "spreadsheet chunks"
            )
        else:
            # If we already chunked a PDF above, don't overwrite.
            if not ctx.chunks:
                chunker = Chunker(
                    chunk_size=chunking_config["chunk_size"],
                    chunk_overlap=chunking_config["chunk_overlap"],
                )
                ctx.chunks = chunker.chunk_text(ctx.extracted_text)

        if not ctx.chunks:
            raise ValueError("No chunks generated from document")

        # Update stage to completed
        await _update_stage_status(
            db,
            stage_record.id,
            StageStatus.COMPLETED,
            input_size=len(ctx.extracted_text),
            output_size=len(ctx.chunks),
        )


# ==================== Stage 4: Generate Embeddings ====================


@celery_app.task(name="pipeline.generate_embeddings", bind=True, max_retries=3)
def step4_generate_embeddings(self, context_dict: dict[str, Any]) -> dict[str, Any]:
    """
    Stage 4: Generate embeddings for chunks.

    Updates:
    - Redis: stage="embedding", status="in_progress"
    - PostgreSQL: DocumentProcessingStage record

    Returns:
        Updated context dictionary with embeddings
    """
    ctx = PipelineContext.from_dict(context_dict)
    redis_tracker = get_redis_tracker()
    loop = get_event_loop()

    try:
        logger.info(f"[Doc {ctx.document_id}] Stage 4: Generating embeddings")

        # Update Redis status
        redis_tracker.set_document_status(
            ctx.document_id,
            stage="embedding",
            status="in_progress",
            progress=60.0,
        )
        redis_tracker.update_stage(ctx.document_id, "embedding", "in_progress")

        # Run async operations
        loop.run_until_complete(_step4_embed_async(ctx, redis_tracker))

        logger.info(
            f"[Doc {ctx.document_id}] Stage 4 completed: Generated {len(ctx.embeddings)} embeddings"
        )

        # Update Redis status
        redis_tracker.update_stage(ctx.document_id, "embedding", "completed")
        redis_tracker.set_document_status(
            ctx.document_id,
            stage="embedding",
            status="completed",
            progress=80.0,
            metadata={"embeddings_generated": len(ctx.embeddings)},
        )

        return ctx.to_dict()

    except Exception as exc:
        logger.error(f"[Doc {ctx.document_id}] Stage 4 failed: {exc}", exc_info=True)

        # Update Redis with error
        redis_tracker.update_stage(ctx.document_id, "embedding", "failed")
        redis_tracker.log_error(
            ctx.document_id,
            stage="embedding",
            error=str(exc),
            retry_count=self.request.retries,
        )

        # Update PostgreSQL
        loop.run_until_complete(_mark_stage_failed(ctx, "embedding", str(exc)))

        # Only cleanup temp file on final retry failure
        if self.request.retries >= self.max_retries:
            if ctx.temp_file_path and os.path.exists(ctx.temp_file_path):
                logger.info(
                    f"[Doc {ctx.document_id}] Cleaning up temp file after final retry"
                )
                os.remove(ctx.temp_file_path)

        # Retry
        raise self.retry(exc=exc, countdown=60 * (2**self.request.retries)) from exc


async def _step4_embed_async(ctx: PipelineContext, redis_tracker):
    """Async implementation of embedding generation."""
    async with AsyncSessionLocal() as db:
        # Create stage record
        stage_record = await _create_stage_record(
            db, ctx.document_id, ctx.job_id, ProcessingStage.EMBEDDING
        )
        ctx.stage_ids["embedding"] = stage_record.id

        # Update stage to in_progress
        await _update_stage_status(db, stage_record.id, StageStatus.IN_PROGRESS)

        # Get default chatbot config for embedding model
        chatbot_config_obj = await chatbot_config_service.get_default_chatbot_config(
            db, ctx.tenant_id
        )

        if not chatbot_config_obj:
            raise ValueError(
                f"Default chatbot config not configured for tenant {ctx.tenant_id}"
            )

        # Get embedding model config from version history
        embedding_config = await chatbot_config_service.get_embedding_model_config(
            db, chatbot_config_obj.id
        )
        embedding_model_name = embedding_config.get("model")
        if not embedding_model_name:
            raise ValueError(
                f"Embedding model not configured for chatbot {chatbot_config_obj.id}"
            )

        logger.info(
            f"[Doc {ctx.document_id}] Using embedding model: {embedding_model_name} "
            f"(provider: {embedding_config.get('provider', 'unknown')})"
        )

        # Generate embeddings
        embedding_service = EmbeddingService()
        ctx.embeddings = await embedding_service.generate_embeddings(
            ctx.chunks,
            model=embedding_model_name,
        )

        if len(ctx.embeddings) != len(ctx.chunks):
            raise ValueError(
                f"Embedding count mismatch: {len(ctx.embeddings)} != {len(ctx.chunks)}"
            )

        # Update stage to completed
        await _update_stage_status(
            db,
            stage_record.id,
            StageStatus.COMPLETED,
            input_size=len(ctx.chunks),
            output_size=len(ctx.embeddings),
        )


# ==================== Stage 5: Store in Vector DB ====================


@celery_app.task(name="pipeline.store_vectors", bind=True, max_retries=3)
def step5_store_vectors(self, context_dict: dict[str, Any]) -> dict[str, Any]:
    """
    Stage 5: Store chunks and embeddings in vector database.

    Updates:
    - Redis: stage="storing", status="in_progress"
    - PostgreSQL: DocumentProcessingStage record
    - Document: processing_status="completed", chunk_count

    Returns:
        Updated context dictionary (final)
    """
    ctx = PipelineContext.from_dict(context_dict)
    redis_tracker = get_redis_tracker()
    loop = get_event_loop()

    try:
        logger.info(f"[Doc {ctx.document_id}] Stage 5: Storing in vector database")

        # Update Redis status
        redis_tracker.set_document_status(
            ctx.document_id,
            stage="storing",
            status="in_progress",
            progress=80.0,
        )
        redis_tracker.update_stage(ctx.document_id, "storing", "in_progress")

        # Run async operations
        loop.run_until_complete(_step5_store_async(ctx, redis_tracker))

        logger.info(
            f"[Doc {ctx.document_id}] Stage 5 completed: Stored {len(ctx.chunks)} chunks"
        )

        # Update Redis status
        redis_tracker.update_stage(ctx.document_id, "storing", "completed")
        redis_tracker.set_document_status(
            ctx.document_id,
            stage="completed",
            status="completed",
            progress=100.0,
            metadata={"chunks_stored": len(ctx.chunks)},
        )

        # Remove from active jobs
        redis_tracker.remove_active_job(ctx.document_id)

        # Cleanup temp file
        if ctx.temp_file_path and os.path.exists(ctx.temp_file_path):
            os.remove(ctx.temp_file_path)

        logger.info(f"[Doc {ctx.document_id}] ✅ Pipeline completed successfully!")

        return ctx.to_dict()

    except Exception as exc:
        logger.error(f"[Doc {ctx.document_id}] Stage 5 failed: {exc}", exc_info=True)

        # Update Redis with error
        redis_tracker.update_stage(ctx.document_id, "storing", "failed")
        redis_tracker.log_error(
            ctx.document_id,
            stage="storing",
            error=str(exc),
            retry_count=self.request.retries,
        )

        # Update PostgreSQL
        loop.run_until_complete(_mark_stage_failed(ctx, "storing", str(exc)))

        # Only cleanup temp file on final retry failure
        if self.request.retries >= self.max_retries:
            if ctx.temp_file_path and os.path.exists(ctx.temp_file_path):
                logger.info(
                    f"[Doc {ctx.document_id}] Cleaning up temp file after final retry"
                )
                os.remove(ctx.temp_file_path)

        # Retry
        raise self.retry(exc=exc, countdown=60 * (2**self.request.retries)) from exc


async def _step5_store_async(ctx: PipelineContext, redis_tracker):
    """Async implementation of vector storage."""
    async with AsyncSessionLocal() as db:
        # Create stage record
        stage_record = await _create_stage_record(
            db, ctx.document_id, ctx.job_id, ProcessingStage.STORING
        )
        ctx.stage_ids["storing"] = stage_record.id

        # Update stage to in_progress
        await _update_stage_status(db, stage_record.id, StageStatus.IN_PROGRESS)

        # Prepare metadata for vector store.
        # Strip internal pipeline keys before spreading onto every chunk.
        doc_meta_clean = {
            k: v
            for k, v in ctx.doc_metadata.items()
            if not k.startswith("_xlsx_")
        }
        document = await db.get(Document, ctx.document_id)
        metadatas = []
        for i, _chunk_text in enumerate(ctx.chunks):
            # Per-chunk metadata (e.g. sheet_name for spreadsheets) takes precedence.
            per_chunk = (
                ctx.chunk_metadatas[i]
                if ctx.chunk_metadatas and i < len(ctx.chunk_metadatas)
                else {}
            )
            chunk_metadata = {
                "document_id": ctx.doc_uuid,
                "document_name": document.name,
                "chunk_index": i,
                "tenant_id": ctx.tenant_id,
                "document_type": ctx.document_type,
                "created_at": datetime.utcnow().isoformat(),
                "s3_url": document.s3_url,
                **doc_meta_clean,
                **per_chunk,
            }
            metadatas.append(chunk_metadata)

        # Store in vector database
        vector_store = VectorStoreFactory.create(
            VectorStoreType(settings.VECTOR_STORE_TYPE)
        )
        await vector_store.add_chunks(
            document_id=ctx.doc_uuid,
            chunks=ctx.chunks,
            embeddings=ctx.embeddings,
            metadatas=metadatas,
        )

        # Process images if available and if using Qdrant (for multimodal RAG)
        extracted_images = ctx.doc_metadata.get("extracted_images", [])
        if extracted_images and settings.VECTOR_STORE_TYPE == "qdrant":
            try:
                await _process_images(
                    db=db,
                    ctx=ctx,
                    document=document,
                    extracted_images=extracted_images,
                    vector_store=vector_store,
                )
            except Exception as e:
                logger.warning(
                    f"[Doc {ctx.document_id}] Image processing failed: {e}",
                    exc_info=True,
                )
                # Don't fail the entire pipeline if image processing fails

        # Update document status and clear any previous error
        document.processing_status = ProcessingStatus.COMPLETED
        document.chunk_count = len(ctx.chunks)
        document.error_message = None
        await db.commit()

        # Update job status
        job = await db.get(DocumentProcessingJob, ctx.job_id)
        if job:
            job.status = JobStatus.COMPLETED
            job.chunks_created = len(ctx.chunks)
            await db.commit()

        # Update stage to completed
        await _update_stage_status(
            db,
            stage_record.id,
            StageStatus.COMPLETED,
            input_size=len(ctx.embeddings),
            output_size=len(ctx.chunks),
        )


# ==================== Image Processing ====================


async def _process_images(
    db: AsyncSession,
    ctx: PipelineContext,
    document: Document,
    extracted_images: list[dict[str, Any]],
    vector_store: Any,
):
    """
    Process images: generate captions, embeddings, and store in database and Qdrant.

    Args:
        db: Database session
        ctx: Pipeline context
        document: Document object
        extracted_images: List of extracted image metadata
        vector_store: Vector store instance
    """
    from app.models.image_captions import ImageCaption
    from app.services.embeddings.embedding_service import EmbeddingService

    logger.info(
        f"[Doc {ctx.document_id}] Processing {len(extracted_images)} images"
    )

    image_caption_service = ImageCaptionService()
    embedding_service = EmbeddingService()

    image_captions_list = []
    image_caption_texts = []
    image_embeddings_list = []
    image_metadatas = []

    for idx, image_meta in enumerate(extracted_images):
        try:
            image_path = image_meta.get("file_path")
            if not image_path or not os.path.exists(image_path):
                logger.warning(
                    f"[Doc {ctx.document_id}] Image {idx} file not found: {image_path}"
                )
                continue

            # Generate caption using GPT-4o vision
            surrounding_before = image_meta.get("surrounding_text_before")
            surrounding_after = image_meta.get("surrounding_text_after")
            caption = await image_caption_service.generate_caption(
                image_path=image_path,
                surrounding_text_before=surrounding_before,
                surrounding_text_after=surrounding_after,
            )

            # Create image caption record
            image_caption = ImageCaption(
                document_id=ctx.document_id,
                tenant_id=ctx.tenant_id,
                image_file_path=image_path,
                page_number=image_meta.get("page_number", 0),
                image_index=image_meta.get("image_index", idx + 1),
                caption=caption,
                surrounding_text_before=surrounding_before,
                surrounding_text_after=surrounding_after,
                width=image_meta.get("width"),
                height=image_meta.get("height"),
                size_bytes=image_meta.get("size_bytes"),
            )
            db.add(image_caption)
            await db.flush()  # Flush to get the ID

            # Create combined text for embedding (surrounding context + caption)
            # This ensures queries matching surrounding context will retrieve the image
            parts = []
            if surrounding_before:
                parts.append(surrounding_before.strip())
            parts.append(caption.strip())
            if surrounding_after:
                parts.append(surrounding_after.strip())
            
            combined_text = " ".join(parts)
            
            # Truncate if too long (embedding models have token limits)
            # Conservative limit of 8000 characters to stay well within token limits
            if len(combined_text) > 8000:
                # Keep caption, truncate context equally
                caption_len = len(caption)
                remaining_chars = 8000 - caption_len - 2  # -2 for spaces
                if remaining_chars > 0:
                    chars_per_context = remaining_chars // 2
                    before_truncated = surrounding_before[:chars_per_context] if surrounding_before else ""
                    after_truncated = surrounding_after[:chars_per_context] if surrounding_after else ""
                    combined_text = f"{before_truncated} {caption} {after_truncated}".strip()
                else:
                    # If caption itself is too long, just use caption
                    combined_text = caption[:8000]
            
            # Prepare for embedding and vector storage
            image_captions_list.append(image_caption)
            image_caption_texts.append(combined_text)  # Use combined text for embedding
            image_metadatas.append(
                {
                    "document_id": ctx.doc_uuid,
                    "document_name": document.name,
                    "tenant_id": ctx.tenant_id,
                    "image_caption_id": image_caption.id,
                    "image_file_path": image_path,
                    "page_number": image_meta.get("page_number", 0),
                    "image_index": image_meta.get("image_index", idx + 1),
                    "created_at": datetime.utcnow().isoformat(),
                    # Store surrounding text and caption separately for metadata
                    "caption": caption,  # Original GPT-generated caption
                    "surrounding_text_before": surrounding_before,
                    "surrounding_text_after": surrounding_after,
                    "combined_text": combined_text,  # The actual text that was embedded
                }
            )

            logger.info(
                f"[Doc {ctx.document_id}] Generated caption for image {idx + 1}: {caption[:50]}..."
            )

        except Exception as e:
            logger.error(
                f"[Doc {ctx.document_id}] Error processing image {idx}: {e}",
                exc_info=True,
            )
            continue

    if not image_caption_texts:
        logger.warning(f"[Doc {ctx.document_id}] No image captions generated")
        return

    # Generate embeddings for image captions
    try:
        image_embeddings = await embedding_service.generate_embeddings(
            image_caption_texts
        )
        image_embeddings_list = image_embeddings
    except Exception as e:
        logger.error(
            f"[Doc {ctx.document_id}] Error generating image embeddings: {e}",
            exc_info=True,
        )
        return

    # Store image captions in Qdrant (if QdrantStore)
    if hasattr(vector_store, "add_image_captions"):
        try:
            await vector_store.add_image_captions(
                document_id=ctx.doc_uuid,
                image_captions=image_caption_texts,
                embeddings=image_embeddings_list,
                metadatas=image_metadatas,
            )
            logger.info(
                f"[Doc {ctx.document_id}] Stored {len(image_caption_texts)} image captions in Qdrant"
            )
        except Exception as e:
            logger.error(
                f"[Doc {ctx.document_id}] Error storing image captions in Qdrant: {e}",
                exc_info=True,
            )

    # Commit image caption records
    await db.commit()
    logger.info(
        f"[Doc {ctx.document_id}] Successfully processed {len(image_captions_list)} images "
        f"(stored {len(image_caption_texts)} embeddings)"
    )


# ==================== Error Handling ====================


async def _mark_stage_failed(
    ctx: PipelineContext, stage_name: str, error_message: str
) -> None:
    """Mark a stage as failed in PostgreSQL."""
    async with AsyncSessionLocal() as db:
        stage_id = ctx.stage_ids.get(stage_name)
        if stage_id:
            await _update_stage_status(
                db,
                stage_id,
                StageStatus.FAILED,
                error_message=error_message,
                error_traceback=traceback.format_exc(),
            )

        # Update document and job status with error message
        failure_msg = f"Failed at {stage_name}: {error_message}"
        await _update_document_status(
            db, ctx.document_id, ProcessingStatus.FAILED, error_message=failure_msg
        )
        await _update_job_status(db, ctx.job_id, JobStatus.FAILED)

        job = await db.get(DocumentProcessingJob, ctx.job_id)
        if job:
            job.error_message = failure_msg
            await db.commit()


# ==================== Pipeline Orchestrator ====================


@celery_app.task(name="pipeline.process_document")
def process_document_pipeline(
    document_id: int, job_id: int, batch_id: int | None = None
) -> None:
    """
    Orchestrate the entire document processing pipeline using Celery Canvas.

    This creates a chain of tasks that execute sequentially:
    1. Download from S3
    2. Extract text
    3. Chunk text
    4. Generate embeddings
    5. Store in vector DB

    Each task passes its context to the next task in the chain.

    Args:
        document_id: Document ID
        job_id: Processing job ID
        batch_id: Optional batch ID for tracking
    """
    logger.info(
        f"[Doc {document_id}] Starting pipeline orchestration (job_id={job_id}, batch_id={batch_id})"
    )

    # Create initial context
    ctx = PipelineContext(document_id, job_id, batch_id)

    # Build the pipeline chain
    pipeline = chain(
        step1_download_from_s3.s(ctx.to_dict()),
        step2_extract_text.s(),
        step2_5_summarize_document.s(),
        step2_6_extract_heatmap_metadata.s(),
        step3_chunk_text.s(),
        step4_generate_embeddings.s(),
        step5_store_vectors.s(),
    )

    # Execute the pipeline asynchronously
    result = pipeline.apply_async()

    logger.info(f"[Doc {document_id}] Pipeline queued with chain ID: {result.id}")

    return result.id
