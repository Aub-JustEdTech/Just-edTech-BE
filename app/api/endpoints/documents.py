"""
Document management endpoints for uploading and managing RAG documents.
"""

import os
from pathlib import Path

import httpx
from fastapi import (
    APIRouter,
    Depends,
    File,
    HTTPException,
    Query,
    UploadFile,
    status,
)
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.chat_consumers import ChatConsumer
from app.models.documents import Document, ProcessingStatus
from app.models.processing_jobs import DocumentProcessingJob
from app.schemas.documents import (
    DocumentBulkDeleteFailure,
    DocumentBulkDeleteRequest,
    DocumentBulkDeleteResponse,
    DocumentDetailResponse,
    DocumentListResponse,
    DocumentScrapeRequest,
    DocumentScrapeResponse,
    DocumentSearchRequest,
    DocumentSearchResponse,
    DocumentSortField,
    DocumentUploadResponse,
    PresignedUrlResponse,
    ProcessingJobResponse,
    SearchResult,
    SortOrder,
)
from app.schemas.documents import MediaLinkIngestRequest, MediaUsageResponse
from app.schemas.users import User
from app.services.document_service import DocumentService
from app.services.media_ingest_service import (
    PENDING_MEDIA_DOCTYPES,
    is_media_extension,
    media_ingest_service,
)
from app.services.media_usage_service import (
    MediaQuotaExceededError,
    media_usage_service,
)
from app.services.web_scraper import MarkdownConverter, WebScraperService
from app.tasks.document_pipeline import process_document_pipeline
from app.tasks.media_transcription_tasks import transcribe_media_task
from app.utils.dependencies import (
    get_db,
    get_effective_tenant_id,
    require_user_or_chat_consumer,
    resolve_chat_tenant_id,
)
from app.utils.response import success_response
from app.utils.s3 import S3Manager

router = APIRouter()

# Content types used when presigning an object for inline browser display.
# The audio/video entries are what let a citation open a player instead of
# triggering a download.
MIME_BY_EXTENSION = {
    ".pdf": "application/pdf",
    ".md": "text/markdown; charset=utf-8",
    ".txt": "text/plain; charset=utf-8",
    ".text": "text/plain; charset=utf-8",
    ".doc": "application/msword",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".xls": "application/vnd.ms-excel",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    # The stored transcript is a text file, not JSON — see TranscriptResult.
    ".transcript": "text/plain; charset=utf-8",
    # Media
    ".mp3": "audio/mpeg",
    ".m4a": "audio/mp4",
    ".wav": "audio/wav",
    ".mp4": "video/mp4",
    ".mov": "video/quicktime",
    ".webm": "video/webm",
}


def _resolve_playback_target(document: Document) -> tuple[str | None, str | None, str]:
    """Where to point a viewer at this document, and how to serve it.

    Returns ``(s3_key, external_url, content_type)`` — exactly one of the first
    two is set.

    A transcribed media document has had its ``s3_url`` repointed at the
    transcript envelope, because that is what the pipeline consumes. Handing
    that to a player would play nothing, so media resolves to the stored source
    media instead, falling back to the original remote URL for items that were
    never downloaded (scraper items under ``url_direct``, and every link
    ingest, which has no stored copy by design).
    """
    meta = document.source_metadata or {}
    doc_type = (document.document_type or "").lower()

    if doc_type == ".transcript" or doc_type in settings.ALLOWED_MEDIA_TYPES:
        raw_key = meta.get("s3_key_raw")
        if raw_key:
            raw_ext = os.path.splitext(raw_key)[1].lower()
            return raw_key, None, MIME_BY_EXTENSION.get(raw_ext, "application/octet-stream")

        external = meta.get("source_media_url")
        if external:
            # No stored copy exists — the original URL is the only playable
            # pointer. Returned as-is; there is nothing to presign.
            return None, external, "application/octet-stream"

    prefix = f"s3://{settings.S3_BUCKET_NAME}/"
    s3_key = (
        document.s3_url[len(prefix) :]
        if document.s3_url and document.s3_url.startswith(prefix)
        else None
    )
    return s3_key, None, MIME_BY_EXTENSION.get(doc_type, "application/octet-stream")


_document_service: DocumentService | None = None


def get_document_service() -> DocumentService:
    """Lazy initialization of DocumentService to avoid loading heavy dependencies at import time."""
    global _document_service
    if _document_service is None:
        _document_service = DocumentService()
    return _document_service


@router.post(
    "/upload",
    response_model=DocumentUploadResponse,
    status_code=status.HTTP_201_CREATED,
)
async def upload_document(
    file: UploadFile = File(..., description="Document file to upload"),
    db: AsyncSession = Depends(get_db),
    tenant_id: int = Depends(get_effective_tenant_id),
):
    """
    Upload a document or media file for processing and embedding generation.

    Documents: PDF (.pdf), Markdown (.md), Text (.txt, .text), DOCX (.docx),
    DOC (.doc), XLSX (.xlsx), XLS (.xls) — max MAX_FILE_SIZE_MB.

    Media: MP3, MP4, WAV, M4A, WEBM, MOV — max MAX_MEDIA_FILE_SIZE_MB. Media is
    transcribed first (a paid, minutes-long step), then follows the identical
    path as any other document.

    Both end up:
    1. Uploaded to S3 storage
    2. Queued for background processing
    3. Text extracted and chunked
    4. Embeddings generated
    5. Stored in vector database
    """
    # Validate file extension. Media and documents have separate allow-lists
    # and separate size ceilings, so the branch is decided here and honoured
    # by everything below it.
    file_extension = os.path.splitext(file.filename)[1].lower()
    is_media = is_media_extension(file.filename)
    if not is_media and file_extension not in settings.ALLOWED_DOCUMENT_TYPES:
        supported = ", ".join(
            [*settings.ALLOWED_DOCUMENT_TYPES, *settings.ALLOWED_MEDIA_TYPES]
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"File type {file_extension} not allowed. Supported types: {supported}",
        )

    # Get file size without reading entire file into memory
    try:
        file.file.seek(0, 2)
        file_size = file.file.tell()
        file.file.seek(0)
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get file size: {str(e)}",
        ) from e

    # Validate file size
    max_mb = (
        settings.MAX_MEDIA_FILE_SIZE_MB if is_media else settings.MAX_FILE_SIZE_MB
    )
    if file_size > max_mb * 1024 * 1024:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"File size exceeds {settings.MAX_FILE_SIZE_MB}MB limit",
        )

    if is_media:
        return await _ingest_media_upload(
            db=db,
            file=file,
            tenant_id=tenant_id,
            file_size=file_size,
        )

    # Upload document using streaming (memory-efficient)
    try:
        document = await get_document_service().upload_document_stream(
            db=db,
            fileobj=file.file,
            file_name=file.filename,
            tenant_id=tenant_id,
            file_size=file_size,
        )

        # Get the job ID from the database
        await db.refresh(document)
        job_result = await db.execute(
            select(DocumentProcessingJob).where(
                DocumentProcessingJob.document_id == document.id
            )
        )
        processing_job = job_result.scalar_one_or_none()

        if processing_job:
            # Queue the document for background processing with Celery Pipeline
            process_document_pipeline.delay(document.id, processing_job.id)

    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        ) from e
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to upload document: {str(e)}",
        ) from e

    return success_response(
        data=DocumentUploadResponse.model_validate(document),
        status_code=status.HTTP_201_CREATED,
    )


async def _ingest_media_upload(
    *,
    db: AsyncSession,
    file: UploadFile,
    tenant_id: int,
    file_size: int,
):
    """Store an uploaded media file and queue it for transcription.

    The quota is checked BEFORE the S3 upload, not after: a tenant who is out
    of budget should be told so in the response, not after pushing 400MB over
    the wire into a bucket the job will then refuse to process.
    """
    try:
        await media_usage_service.assert_within_quota(db, tenant_id)
    except MediaQuotaExceededError as e:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=str(e),
        ) from e

    try:
        document, job = await media_ingest_service.create_upload(
            db,
            fileobj=file.file,
            file_name=file.filename,
            tenant_id=tenant_id,
            file_size=file_size,
        )
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)
        ) from e
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to upload media: {str(e)}",
        ) from e

    transcribe_media_task.delay(document.id, job.id)

    return success_response(
        data=DocumentUploadResponse.model_validate(document),
        status_code=status.HTTP_201_CREATED,
    )


@router.post(
    "/media-link",
    response_model=DocumentUploadResponse,
    status_code=status.HTTP_201_CREATED,
)
async def ingest_media_link(
    payload: MediaLinkIngestRequest,
    db: AsyncSession = Depends(get_db),
    tenant_id: int = Depends(get_effective_tenant_id),
):
    """
    Ingest a YouTube video or a direct audio/video URL by link.

    Nothing is uploaded — the transcription provider fetches the media itself.

    YouTube videos that already have captions (manual or auto) are transcribed
    for **free** and do not count against the tenant's monthly minutes. Only a
    caption-less video, or a direct media URL, reaches the paid path.
    """
    try:
        await media_usage_service.assert_within_quota(db, tenant_id)
    except MediaQuotaExceededError as e:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=str(e)
        ) from e

    try:
        document, job = await media_ingest_service.create_link(
            db,
            url=str(payload.url),
            tenant_id=tenant_id,
            name=payload.name,
        )
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)
        ) from e

    transcribe_media_task.delay(document.id, job.id)

    return success_response(
        data=DocumentUploadResponse.model_validate(document),
        status_code=status.HTTP_201_CREATED,
    )


@router.get("/media-usage", response_model=MediaUsageResponse)
async def get_media_usage(
    db: AsyncSession = Depends(get_db),
    tenant_id: int = Depends(get_effective_tenant_id),
):
    """Transcription minutes used against this tenant's monthly cap."""
    used_seconds = await media_usage_service.get_month_usage_seconds(db, tenant_id)
    limit_minutes = settings.TENANT_MEDIA_MONTHLY_MINUTES_LIMIT
    used_minutes = used_seconds // 60

    return success_response(
        data=MediaUsageResponse(
            used_minutes=used_minutes,
            limit_minutes=limit_minutes,
            remaining_minutes=(
                max(0, limit_minutes - used_minutes) if limit_minutes > 0 else None
            ),
            unlimited=limit_minutes <= 0,
        )
    )


@router.post(
    "/scrape",
    response_model=DocumentScrapeResponse,
    status_code=status.HTTP_201_CREATED,
)
async def scrape_document(
    scrape_request: DocumentScrapeRequest,
    db: AsyncSession = Depends(get_db),
    tenant_id: int = Depends(get_effective_tenant_id),
):
    """
    Scrape a web page and convert it to a document for processing.

    The endpoint will:
    1. Fetch HTML content from the provided URL
    2. Extract metadata (title, description, author)
    3. Convert HTML to Markdown format
    4. Upload as a markdown document
    5. Queue for background processing (chunking, embedding, vector storage)

    **Example:**
    ```bash
    curl -X POST "http://localhost:8000/api/v1/documents/scrape" \\
      -H "Authorization: Bearer YOUR_TOKEN" \\
      -H "Content-Type: application/json" \\
      -d '{
        "url": "https://example.com/article",
        "name": "My Article",
        "include_metadata": true,
        "timeout_seconds": 30
      }'
    ```
    """
    from datetime import datetime
    from urllib.parse import urlparse

    try:
        # Initialize web scraper with custom timeout
        async with WebScraperService(timeout=scrape_request.timeout_seconds, verify_ssl=scrape_request.verify_ssl) as scraper:
            # Fetch HTML content from URL
            try:
                content_data = await scraper.fetch_content(scrape_request.url)
                html_content = content_data["html"]
                final_url = content_data["url"]  # URL after redirects
            except ValueError as e:
                # Invalid URL format
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Invalid URL format: {str(e)}",
                ) from e
            except httpx.TimeoutException as e:
                # Timeout error
                raise HTTPException(
                    status_code=status.HTTP_408_REQUEST_TIMEOUT,
                    detail=f"Request timeout while fetching URL (timeout: {scrape_request.timeout_seconds}s)",
                ) from e
            except httpx.HTTPStatusError as e:
                status_code = e.response.status_code if e.response else "unknown"
                if status_code == 404:
                    raise HTTPException(
                        status_code=status.HTTP_404_NOT_FOUND,
                        detail=f"URL not found (404): {scrape_request.url}",
                    ) from e
                elif status_code == 403:
                    raise HTTPException(
                        status_code=status.HTTP_403_FORBIDDEN,
                        detail=f"Access forbidden (403): {scrape_request.url}",
                    ) from e
                else:
                    raise HTTPException(
                        status_code=status.HTTP_502_BAD_GATEWAY,
                        detail=f"HTTP error {status_code} while fetching URL: {scrape_request.url}",
                    ) from e
            except httpx.NetworkError as e:
                # Network errors (DNS failure, connection errors)
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    detail=f"Network error while fetching URL: {str(e)}",
                ) from e
            except httpx.RequestError as e:
                # Other request errors
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    detail=f"Request error while fetching URL: {str(e)}",
                ) from e

            # Convert HTML to Markdown
            converter = MarkdownConverter()

            # Extract metadata if requested
            metadata = {}
            if scrape_request.include_metadata:
                metadata = converter.extract_metadata(html_content, final_url)

            # Generate document name
            # Priority: 1. Label from frontend (name), 2. Title from metadata, 3. URL-based name
            if scrape_request.name:
                # Use label from frontend if provided
                document_name = scrape_request.name
            elif metadata.get("title") and metadata["title"] != "Untitled":
                # Use extracted title if available and not "Untitled"
                document_name = metadata["title"]
            else:
                # Fallback: Generate name from URL
                parsed_url = urlparse(final_url)
                domain = parsed_url.netloc.replace("www.", "")
                path = parsed_url.path.strip("/").replace("/", "_")
                if path:
                    document_name = f"{domain}_{path}"
                else:
                    document_name = domain
                # Limit name length
                if len(document_name) > 100:
                    document_name = document_name[:100]

            # Ensure .md extension
            if not document_name.endswith(".md"):
                document_name = f"{document_name}.md"

            # Format markdown document
            markdown_content = converter.format_markdown_document(
                html_content,
                final_url,
                metadata if scrape_request.include_metadata else None,
                include_metadata=scrape_request.include_metadata,
            )

            # Convert to bytes
            markdown_bytes = markdown_content.encode("utf-8")
            content_length = len(markdown_bytes)

            # Validate content size (use same limit as file uploads)
            max_size = settings.MAX_FILE_SIZE_MB * 1024 * 1024
            if content_length > max_size:
                raise HTTPException(
                    status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                    detail=f"Scraped content exceeds {settings.MAX_FILE_SIZE_MB}MB limit",
                )

            # Upload document using DocumentService
            try:
                document = await get_document_service().upload_document(
                    db=db,
                    file_content=markdown_bytes,
                    file_name=document_name,
                    tenant_id=tenant_id,
                    file_size=content_length,
                )

                # Get the job ID from the database
                await db.refresh(document)
                job_result = await db.execute(
                    select(DocumentProcessingJob).where(
                        DocumentProcessingJob.document_id == document.id
                    )
                )
                processing_job = job_result.scalar_one_or_none()

                if processing_job:
                    # Queue the document for background processing with Celery Pipeline
                    process_document_pipeline.delay(document.id, processing_job.id)

            except ValueError as e:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=str(e),
                ) from e
            except Exception as e:
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail=f"Failed to upload scraped document: {str(e)}",
                ) from e

            # Build response with additional fields
            scraped_at = datetime.utcnow()
            response_data = DocumentScrapeResponse(
                id=document.id,
                name=document.name,
                doc_id=document.doc_id,
                document_type=document.document_type,
                processing_status=document.processing_status,
                file_size_bytes=document.file_size_bytes,
                tenant_id=document.tenant_id,
                created_at=document.created_at,
                source_url=final_url,
                scraped_at=scraped_at,
                content_length=content_length,
                metadata=metadata if scrape_request.include_metadata else {},
            )

            return response_data
    except HTTPException:
        # Re-raise HTTP exceptions (they're already properly formatted)
        raise
    except Exception as e:
        # Catch any unexpected errors
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Unexpected error while scraping document: {str(e)}",
        ) from e


@router.post(
    "/bulk-upload",
    response_model=list[DocumentUploadResponse],
    status_code=status.HTTP_201_CREATED,
)
async def bulk_upload_documents(
    files: list[UploadFile] = File(
        ...,
        description=(
            "Multiple document files to upload "
            f"(max {settings.BULK_UPLOAD_MAX_FILES} per request)"
        ),
    ),
    batch_id: str | None = Query(
        None, description="Optional: Batch ID for tracking bulk uploads"
    ),
    db: AsyncSession = Depends(get_db),
    tenant_id: int = Depends(get_effective_tenant_id),
):
    """
    Upload multiple documents at once for processing and embedding generation.

    **THIS IS THE ENDPOINT TO USE FOR UPLOADING MULTIPLE FILES**

    Supported file types: PDF (.pdf), Markdown (.md), Text (.txt, .text), DOCX (.docx), DOC (.doc)
    Maximum file size per file: Configured in settings (default 50MB)

    Each document will be:
    1. Uploaded to S3 storage
    2. Queued for background processing (parallel)
    3. Text extracted and chunked
    4. Embeddings generated
    5. Stored in vector database

    **Example Without Batch Tracking:**
    ```bash
    curl -X POST "http://localhost:8000/api/v1/documents/bulk-upload" \\
      -H "Authorization: Bearer YOUR_TOKEN" \\
      -F "files=@doc1.pdf" \\
      -F "files=@doc2.pdf" \\
      -F "files=@doc3.md" \\
      -F "files=@doc4.txt"
    ```

    **Example With Batch Tracking (Recommended when uploading multiple batches of up to 10 files):**
    ```bash
    # Step 1: Create a batch
    BATCH_ID=$(curl -X POST "http://localhost:8000/api/v1/batches/" \\
      -H "Authorization: Bearer YOUR_TOKEN" \\
      -H "Content-Type: application/json" \\
      -d '{"description": "Q4 2024 Reports"}' | jq -r '.batch_id')

    # Step 2: Upload with batch_id
    curl -X POST "http://localhost:8000/api/v1/documents/bulk-upload?batch_id=$BATCH_ID" \\
      -H "Authorization: Bearer YOUR_TOKEN" \\
      -F "files=@doc1.pdf" \\
      -F "files=@doc2.pdf"
      # ... up to 200 files

    # Step 3: Monitor progress (1 API call instead of 200!)
    curl "http://localhost:8000/api/v1/batches/$BATCH_ID/status" \\
      -H "Authorization: Bearer YOUR_TOKEN"
    # Returns: {"summary": {"total": 200, "completed": 150, "progress_percentage": 75.0}}
    ```

    **Returns:** List of created documents with their processing status
    """
    if not files:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No files provided",
        )

    if len(files) > settings.BULK_UPLOAD_MAX_FILES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Too many files provided in a single request. "
            f"Maximum allowed is {settings.BULK_UPLOAD_MAX_FILES}, but received {len(files)}.",
        )

    # Validate batch_id if provided
    upload_batch = None
    upload_batch_db_id = None
    if batch_id:
        from app.crud import upload_batches as crud_batches

        upload_batch = await crud_batches.get_batch(
            db=db, batch_id=batch_id, tenant_id=tenant_id
        )
        if not upload_batch:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Batch with ID {batch_id} not found",
            )
        upload_batch_db_id = upload_batch.id

    uploaded_documents = []
    failed_uploads = []

    # Process each file
    for file in files:
        try:
            # Validate file extension. Media has its own allow-list, size cap
            # and ingest path — see _ingest_media_upload.
            file_extension = os.path.splitext(file.filename)[1].lower()
            is_media = is_media_extension(file.filename)
            if not is_media and file_extension not in settings.ALLOWED_DOCUMENT_TYPES:
                supported = ", ".join(
                    [*settings.ALLOWED_DOCUMENT_TYPES, *settings.ALLOWED_MEDIA_TYPES]
                )
                failed_uploads.append(
                    {
                        "filename": file.filename,
                        "error": f"File type {file_extension} not allowed. Supported types: {supported}",
                    }
                )
                continue

            # Get file size without reading entire file into memory
            try:
                file.file.seek(0, 2)
                file_size = file.file.tell()
                file.file.seek(0)
            except Exception as e:
                failed_uploads.append(
                    {
                        "filename": file.filename,
                        "error": f"Failed to get file size: {str(e)}",
                    }
                )
                continue

            # Validate file size
            max_mb = (
                settings.MAX_MEDIA_FILE_SIZE_MB
                if is_media
                else settings.MAX_FILE_SIZE_MB
            )
            if file_size > max_mb * 1024 * 1024:
                failed_uploads.append(
                    {
                        "filename": file.filename,
                        "error": f"File size exceeds {max_mb}MB limit",
                    }
                )
                continue

            if is_media:
                # Media goes to transcription first, not the document pipeline.
                # The quota is checked per file rather than once for the batch:
                # a batch that starts inside budget can exhaust it partway, and
                # the files after that point must be refused, not charged.
                try:
                    await media_usage_service.assert_within_quota(db, tenant_id)
                except MediaQuotaExceededError as e:
                    failed_uploads.append(
                        {"filename": file.filename, "error": str(e)}
                    )
                    continue

                media_doc, media_job = await media_ingest_service.create_upload(
                    db,
                    fileobj=file.file,
                    file_name=file.filename,
                    tenant_id=tenant_id,
                    file_size=file_size,
                    upload_batch_id=upload_batch_db_id,
                )
                transcribe_media_task.delay(
                    media_doc.id, media_job.id, upload_batch_db_id
                )
                uploaded_documents.append(media_doc)
                continue

            # Upload document using streaming (memory-efficient)
            document = await get_document_service().upload_document_stream(
                db=db,
                fileobj=file.file,
                file_name=file.filename,
                tenant_id=tenant_id,
                file_size=file_size,
                upload_batch_id=upload_batch_db_id,
            )

            # Get the processing job and queue Celery task
            result = await db.execute(
                select(DocumentProcessingJob)
                .where(DocumentProcessingJob.document_id == document.id)
                .order_by(DocumentProcessingJob.created_at.desc())
                .limit(1)
            )
            processing_job = result.scalar_one_or_none()

            if processing_job:
                # Queue the document for background processing with Celery Pipeline
                # Pass batch_id for status updates
                process_document_pipeline.delay(
                    document.id,
                    processing_job.id,
                    upload_batch_db_id,
                )

            uploaded_documents.append(document)

        except Exception as e:
            failed_uploads.append({"filename": file.filename, "error": str(e)})
            continue

    # If all files failed, return error
    if not uploaded_documents and failed_uploads:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"All {len(failed_uploads)} files failed to upload. Errors: {failed_uploads}",
        )

    # Update batch counts if batch was provided
    if upload_batch:
        from app.crud import upload_batches as crud_batches

        await crud_batches.update_batch_counts(db=db, batch_id=upload_batch.id)

    # If some files succeeded, return them (with warning about failures if any)
    response_data = [
        DocumentUploadResponse.model_validate(doc) for doc in uploaded_documents
    ]
    extra = {"failed_uploads": failed_uploads} if failed_uploads else None
    return success_response(
        data=response_data,
        extra=extra,
        status_code=status.HTTP_201_CREATED,
    )


@router.get("/", response_model=list[DocumentListResponse])
async def list_documents(
    skip: int = Query(0, ge=0, description="Number of documents to skip"),
    limit: int = Query(100, ge=1, le=500, description="Number of documents to return"),
    document_type: str | None = Query(
        None, description="Filter by document type (.pdf, .md, etc.)"
    ),
    processing_status: ProcessingStatus | None = Query(
        None,
        description="Filter by processing status (pending, processing, completed, failed). If not set, failed docs are excluded by default; use include_failed=true to show all.",
    ),
    include_failed: bool = Query(
        False,
        description="If true, include failed documents when no processing_status filter is set. Default false so the main list shows only usable docs (pending/processing/completed).",
    ),
    search: str
    | None = Query(
        None,
        min_length=1,
        description="Case-insensitive search string to match document names",
    ),
    sort_by: DocumentSortField = Query(
        DocumentSortField.CREATED_AT,
        description="Field to sort documents by",
    ),
    sort_order: SortOrder = Query(
        SortOrder.DESC, description="Sort order (ascending or descending)"
    ),
    db: AsyncSession = Depends(get_db),
    tenant_id: int = Depends(get_effective_tenant_id),
):
    """
    List documents for the current user's tenant.

    By default returns only documents that are not failed (pending, processing, or completed),
    so the main list shows "uploaded and usable" docs. Use include_failed=true to include
    failed documents (e.g. for a "Failed uploads" tab with error_message and Reprocess).

    Supports:
    - Filtering by document_type: File extension (.pdf, .md, .txt, .text, .docx, .doc)
    - Filtering by processing_status: pending, processing, completed, failed
    - include_failed: if true, include failed docs when no processing_status filter is set
    - Searching by document name (case-insensitive)
    - Sorting by common document attributes
    """
    try:
        # When no explicit status filter: exclude failed by default so users see only "usable" docs
        exclude_failed = processing_status is None and not include_failed
        documents = await get_document_service().get_documents_by_type(
            db=db,
            tenant_id=tenant_id,
            document_type=document_type,
            status=processing_status,
            exclude_failed=exclude_failed,
            skip=skip,
            limit=limit,
            search=search,
            sort_by=sort_by,
            sort_order=sort_order,
        )
        response_data = [
            DocumentListResponse.model_validate(doc) for doc in documents
        ]
        extra = {
            "skip": skip,
            "limit": limit,
            "total": len(response_data),
            "sort_by": sort_by.value,
            "sort_order": sort_order.value,
            "search": search,
        }
        return success_response(data=response_data, extra=extra)
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to retrieve documents: {str(e)}",
        ) from e


@router.get("/{document_id}", response_model=DocumentDetailResponse)
async def get_document(
    document_id: int,
    db: AsyncSession = Depends(get_db),
    tenant_id: int = Depends(get_effective_tenant_id),
):
    """
    Get detailed information about a specific document.
    """
    document = await db.get(Document, document_id)
    if not document:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Document not found",
        )

    # Verify tenant ownership
    if document.tenant_id != tenant_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to access this document",
        )

    return success_response(
        data=DocumentDetailResponse.model_validate(document)
    )


@router.get("/{document_id}/presigned-url", response_model=PresignedUrlResponse)
async def get_document_presigned_url(
    document_id: int,
    db: AsyncSession = Depends(get_db),
    current_user_or_consumer: User | ChatConsumer = require_user_or_chat_consumer,
    expires_in: int = Query(3600, ge=60, le=86400, description="URL expiry in seconds"),
):
    """
    Generate a presigned HTTP URL for the document's S3 object.

    Returns a temporary URL suitable for browser access (view/download).
    """
    document = await db.get(Document, document_id)
    if not document:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Document not found",
        )

    # Resolved against the document's own tenant, same as get_chatbot -
    # a cross-tenant super_admin/tenant_admin has no tenant_id of their own
    # to compare against directly.
    tenant_id = await resolve_chat_tenant_id(
        current_user_or_consumer, document.tenant_id, db
    )
    if document.tenant_id != tenant_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to access this document",
        )

    # Media resolves to its playable source, not the transcript envelope its
    # s3_url points at after transcription. See _resolve_playback_target.
    s3_key, external_url, content_type = _resolve_playback_target(document)

    if external_url:
        return success_response(
            data=PresignedUrlResponse(url=external_url, expires_in=expires_in)
        )

    if not s3_key:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Document does not have an associated S3 URL",
        )

    try:
        s3_manager = S3Manager(
            bucket_name=settings.S3_BUCKET_NAME,
            region_name=settings.S3_REGION,
            aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
            aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
        )

        # Force inline display; include filename when possible
        safe_name = (document.name or f"document_{document_id}").replace('"', "")
        content_disposition = f'inline; filename="{safe_name}"'

        url = await s3_manager.get_presigned_url(
            s3_key=s3_key,
            expiration=expires_in,
            http_method="GET",
            response_content_type=content_type,
            response_content_disposition=content_disposition,
        )
        return success_response(
            data=PresignedUrlResponse(url=url, expires_in=expires_in)
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to generate presigned URL: {str(e)}",
        ) from e


@router.delete(
    "/bulk-delete",
    response_model=DocumentBulkDeleteResponse,
    status_code=status.HTTP_200_OK,
)
async def bulk_delete_documents(
    payload: DocumentBulkDeleteRequest,
    db: AsyncSession = Depends(get_db),
    tenant_id: int = Depends(get_effective_tenant_id),
):
    """
    Delete multiple documents and associated data.
    """
    seen_ids: set[int] = set()
    unique_document_ids: list[int] = []
    for document_id in payload.document_ids:
        if document_id in seen_ids:
            continue
        seen_ids.add(document_id)
        unique_document_ids.append(document_id)

    deleted_document_ids: list[int] = []
    failed_documents: list[DocumentBulkDeleteFailure] = []

    docs_result = await db.execute(
        select(Document).where(Document.id.in_(unique_document_ids))
    )
    docs_by_id = {doc.id: doc for doc in docs_result.scalars().all()}

    for document_id in unique_document_ids:
        document = docs_by_id.get(document_id)
        if not document:
            failed_documents.append(
                DocumentBulkDeleteFailure(
                    document_id=document_id,
                    reason="Document not found",
                )
            )
            continue

        if document.tenant_id != tenant_id:
            failed_documents.append(
                DocumentBulkDeleteFailure(
                    document_id=document_id,
                    reason="Not authorized to delete this document",
                )
            )
            continue

        try:
            await get_document_service().delete_document_instance(db, document)
            deleted_document_ids.append(document_id)
        except Exception as e:  # pragma: no cover - defensive logging
            failed_documents.append(
                DocumentBulkDeleteFailure(
                    document_id=document_id,
                    reason=f"Failed to delete document: {str(e)}",
                )
            )

    if not deleted_document_ids:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "message": "Failed to delete any documents.",
                "failures": [
                    failure.model_dump() for failure in failed_documents
                ],
            },
        )

    return success_response(
        data=DocumentBulkDeleteResponse(
            deleted_document_ids=deleted_document_ids,
            failed_documents=failed_documents,
        )
    )


@router.delete("/{document_id}", status_code=status.HTTP_200_OK)
async def delete_document(
    document_id: int,
    db: AsyncSession = Depends(get_db),
    tenant_id: int = Depends(get_effective_tenant_id),
):
    """
    Delete a document and all associated data.

    This will:
    1. Delete the file from S3
    2. Delete embeddings from vector store
    3. Delete database records (document and processing jobs)
    """
    # Verify document exists and belongs to tenant
    document = await db.get(Document, document_id)
    if not document:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Document not found",
        )

    if document.tenant_id != tenant_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to delete this document",
        )

    try:
        success = await get_document_service().delete_document(db, document_id, tenant_id)
        if not success:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to delete document",
            )
        return success_response(
            data={"message": "Document deleted successfully"},
            status_code=status.HTTP_200_OK,
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to delete document: {str(e)}",
        ) from e


@router.post("/search", response_model=DocumentSearchResponse)
async def search_documents(
    search_request: DocumentSearchRequest,
    db: AsyncSession = Depends(get_db),
    tenant_id: int = Depends(get_effective_tenant_id),
):
    """
    Perform semantic search across documents.

    Uses embeddings to find relevant document chunks based on the query.
    Results are ranked by semantic similarity (distance).
    """
    # Build filters
    filters = {}
    if search_request.document_types:
        filters["document_type"] = {"$in": search_request.document_types}
    if search_request.document_ids:
        filters["document_id"] = {"$in": search_request.document_ids}

    try:
        results = await get_document_service().search_documents(
            db=db,
            query=search_request.query,
            tenant_id=tenant_id,
            limit=search_request.limit,
            filters=filters if filters else None,
        )

        # Convert to response format
        search_results = [
            SearchResult(
                chunk_id=result["id"],
                text=result["text"],
                document_id=result["metadata"].get("document_id", ""),
                document_name=result["metadata"].get("document_name", ""),
                chunk_index=result["metadata"].get("chunk_index", 0),
                distance=result.get("distance", 0.0),
                metadata=result["metadata"],
            )
            for result in results
        ]

        return success_response(
            data=DocumentSearchResponse(
                query=search_request.query,
                results=search_results,
                total_results=len(search_results),
            )
        )
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        ) from e
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Search failed: {str(e)}",
        ) from e


@router.get(
    "/{document_id}/processing-jobs", response_model=list[ProcessingJobResponse]
)
async def get_document_processing_jobs(
    document_id: int,
    db: AsyncSession = Depends(get_db),
    tenant_id: int = Depends(get_effective_tenant_id),
):
    """
    Get all processing jobs for a document.

    Useful for debugging and tracking document processing history.
    """
    # Verify document exists and belongs to tenant
    document = await db.get(Document, document_id)
    if not document:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Document not found",
        )

    if document.tenant_id != tenant_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to access this document",
        )

    result = await db.execute(
        select(DocumentProcessingJob)
        .where(DocumentProcessingJob.document_id == document_id)
        .order_by(DocumentProcessingJob.created_at.desc())
    )
    jobs = result.scalars().all()

    response_data = [
        ProcessingJobResponse.model_validate(job) for job in jobs
    ]
    return success_response(data=response_data)


@router.get("/{document_id}/chunks")
async def get_document_chunks(
    document_id: int,
    db: AsyncSession = Depends(get_db),
    tenant_id: int = Depends(get_effective_tenant_id),
):
    """
    Get all chunks for a specific document.

    Useful for:
    - Inspecting chunk quality
    - Debugging chunking issues
    - Validating chunk size and overlap
    - Reviewing chunk content before RAG queries
    """
    # Verify document exists and belongs to tenant
    document = await db.get(Document, document_id)
    if not document:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Document not found",
        )

    if document.tenant_id != tenant_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to access this document",
        )

    # Check if document is processed
    if document.processing_status != ProcessingStatus.COMPLETED:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Document is not fully processed yet. Current status: {document.processing_status.value}",
        )

    try:
        # Get chunks from vector store
        chunks = await get_document_service().vector_store.get_document_chunks(
            document_id=document.doc_id,
            tenant_id=tenant_id,
        )

        # Calculate statistics
        chunk_lengths = [len(chunk["text"]) for chunk in chunks]

        return success_response(
            data={
                "document_id": document.id,
                "document_name": document.name,
                "total_chunks": len(chunks),
                "statistics": {
                    "min_length": min(chunk_lengths) if chunk_lengths else 0,
                    "max_length": max(chunk_lengths) if chunk_lengths else 0,
                    "avg_length": sum(chunk_lengths) // len(chunk_lengths)
                    if chunk_lengths
                    else 0,
                    "total_characters": sum(chunk_lengths),
                },
                "chunks": [
                    {
                        "chunk_id": chunk["id"],
                        "chunk_index": chunk["metadata"].get("chunk_index", 0),
                        "text": chunk["text"],
                        "text_length": len(chunk["text"]),
                        "metadata": chunk["metadata"],
                    }
                    for chunk in chunks
                ],
            }
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to retrieve chunks: {str(e)}",
        ) from e


@router.post("/{document_id}/reprocess", status_code=status.HTTP_202_ACCEPTED)
async def reprocess_document(
    document_id: int,
    db: AsyncSession = Depends(get_db),
    tenant_id: int = Depends(get_effective_tenant_id),
):
    """
    Trigger reprocessing of a document.

    Useful when:
    - Previous processing failed
    - Tenant configuration changed (chunk size, embedding model, etc.)
    - Document needs to be re-indexed
    """
    # Verify document exists and belongs to tenant
    document = await db.get(Document, document_id)
    if not document:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Document not found",
        )

    if document.tenant_id != tenant_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to reprocess this document",
        )

    # Create new processing job
    from app.models.processing_jobs import JobStatus

    job = DocumentProcessingJob(
        document_id=document.id,
        status=JobStatus.PENDING,
        processor_type=document.document_type,
    )
    db.add(job)

    # Update document status
    document.processing_status = ProcessingStatus.PENDING
    await db.commit()
    await db.refresh(job)

    # Media that never got a transcript has no text for the pipeline to
    # extract, so it must re-enter at the transcription stage. Media that DID
    # transcribe is already a .transcript document and reprocesses through the
    # normal pipeline — reading the stored envelope, not paying again.
    if document.document_type in PENDING_MEDIA_DOCTYPES:
        transcribe_media_task.delay(document.id, job.id)
        message = "Media queued for re-transcription"
    else:
        process_document_pipeline.delay(document.id, job.id)
        message = "Document queued for reprocessing"

    return success_response(
        data={
            "message": message,
            "document_id": document.id,
            "job_id": job.id,
        },
        status_code=status.HTTP_202_ACCEPTED,
    )


@router.get("/url/{doc_ref}")
async def get_document_url_by_ref(
    doc_ref: str,
    db: AsyncSession = Depends(get_db),
    tenant_id: int = Depends(get_effective_tenant_id),
    expires_in: int = Query(3600, ge=60, le=86400, description="URL expiry in seconds"),
):
    """
    Return a presigned S3 URL for a document identified by doc_id (UUID) or name (filename).
    Used by the HeatMap transcript viewer to render the raw PDF directly in the browser.
    """
    result = await db.execute(
        select(Document)
        .where(Document.doc_id == doc_ref, Document.tenant_id == tenant_id)
        .limit(1)
    )
    document = result.scalar_one_or_none()

    if not document:
        result = await db.execute(
            select(Document)
            .where(Document.name == doc_ref, Document.tenant_id == tenant_id)
            .limit(1)
        )
        document = result.scalar_one_or_none()

    if not document:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")

    # Media resolves to its playable source, not the transcript envelope.
    s3_key, external_url, content_type = _resolve_playback_target(document)

    if external_url:
        return success_response(
            data={
                "title": document.name,
                "url": external_url,
                "expires_in": expires_in,
            }
        )

    if not s3_key:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Document does not have an associated S3 URL",
        )

    try:
        s3_manager = S3Manager(
            bucket_name=settings.S3_BUCKET_NAME,
            region_name=settings.S3_REGION,
            aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
            aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
        )
        safe_name = (document.name or f"document_{document.id}").replace('"', "")
        url = await s3_manager.get_presigned_url(
            s3_key=s3_key,
            expiration=expires_in,
            http_method="GET",
            response_content_type=content_type,
            response_content_disposition=f'inline; filename="{safe_name}"',
        )
        return success_response(
            data={"title": document.name, "url": url, "expires_in": expires_in}
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to generate presigned URL: {str(e)}",
        ) from e


@router.get("/text/{doc_ref}")
async def get_document_text(
    doc_ref: str,
    db: AsyncSession = Depends(get_db),
    tenant_id: int = Depends(get_effective_tenant_id),
):
    """
    Return the content of a document. Used by the HeatMap "View full transcript" flow.

    For a ``.transcript`` document, returns ``{title, url, expires_in}`` — a presigned,
    inline-display S3 URL to the stored transcript file. For every other document type,
    returns the full text assembled from its Qdrant chunks, grouped by page.

    doc_ref may be either the document's doc_id (UUID) or its name/filename — whichever
    the citation carries. Sample data uses the filename; live Qdrant data uses the UUID.
    """
    # Try by doc_id first; if not found, fall back to matching by name (filename).
    result = await db.execute(
        select(Document)
        .where(Document.doc_id == doc_ref, Document.tenant_id == tenant_id)
        .limit(1)
    )
    document = result.scalar_one_or_none()

    if not document:
        result = await db.execute(
            select(Document)
            .where(Document.name == doc_ref, Document.tenant_id == tenant_id)
            .limit(1)
        )
        document = result.scalar_one_or_none()

    if not document:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")

    if document.processing_status != ProcessingStatus.COMPLETED:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Document not fully processed yet. Status: {document.processing_status.value}",
        )

    if document.document_type == ".transcript":
        # Redirect to the stored transcript file directly, instead of
        # reassembling it from Qdrant chunks — a presigned, inline-display
        # URL means "View Transcript" opens the text in the browser rather
        # than triggering a download.
        prefix = f"s3://{settings.S3_BUCKET_NAME}/"
        s3_key = (
            document.s3_url[len(prefix) :]
            if document.s3_url and document.s3_url.startswith(prefix)
            else None
        )
        if not s3_key:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Transcript has no associated S3 object",
            )
        try:
            s3_manager = S3Manager(
                bucket_name=settings.S3_BUCKET_NAME,
                region_name=settings.S3_REGION,
                aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
                aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
            )
            safe_name = (document.name or f"document_{document.id}").replace('"', "")
            url = await s3_manager.get_presigned_url(
                s3_key=s3_key,
                expiration=3600,
                http_method="GET",
                response_content_type="text/plain; charset=utf-8",
                response_content_disposition=f'inline; filename="{safe_name}.txt"',
            )
        except Exception as e:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to generate presigned URL: {str(e)}",
            ) from e
        return success_response(
            data={"title": document.name, "url": url, "expires_in": 3600}
        )

    try:
        chunks = await get_document_service().vector_store.get_document_chunks(
            document_id=document.doc_id,
            tenant_id=tenant_id,
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to retrieve document chunks: {str(e)}",
        ) from e

    # Group chunks by page_number; fall back to chunk_index as a synthetic page
    pages: dict[int, list[str]] = {}
    for chunk in chunks:
        meta = chunk.get("metadata", {})
        page = int(meta.get("page_number", meta.get("chunk_index", 1)))
        pages.setdefault(page, []).append(chunk["text"])

    return success_response(
        data={
            "title": document.name,
            "pages": [
                {"page_number": pn, "text": "\n\n".join(texts)}
                for pn, texts in sorted(pages.items())
            ],
        }
    )


@router.get("/images/{image_filename}")
async def get_document_image(
    image_filename: str,
    current_user_or_consumer: User | ChatConsumer = require_user_or_chat_consumer,
):
    """
    Serve extracted document images.
    
    Note: This is a simple implementation. In production, consider:
    - Adding proper access control (check document ownership)
    - Using CDN or S3 presigned URLs
    - Image caching headers
    """
    # Extract tenant_id based on authentication type
    if isinstance(current_user_or_consumer, ChatConsumer):
        tenant_id = current_user_or_consumer.tenant_id
    else:  # User
        tenant_id = current_user_or_consumer.tenant_id
    
    # Construct image path
    image_path = Path(settings.IMAGE_STORAGE_DIR) / image_filename
    
    # Security: Ensure path doesn't escape IMAGE_STORAGE_DIR
    try:
        image_path = image_path.resolve()
        storage_dir = Path(settings.IMAGE_STORAGE_DIR).resolve()
        
        if not str(image_path).startswith(str(storage_dir)):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access denied"
            )
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid image path"
        )
    
    # Check if file exists
    if not image_path.exists() or not image_path.is_file():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Image not found"
        )
    
    # Return image file
    return FileResponse(
        path=str(image_path),
        media_type="image/png",  # Adjust based on actual image type
        headers={
            "Cache-Control": "public, max-age=3600"  # Cache for 1 hour
        }
    )
