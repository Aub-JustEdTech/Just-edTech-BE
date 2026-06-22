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
from app.schemas.users import User
from app.services.document_service import DocumentService
from app.services.web_scraper import MarkdownConverter, WebScraperService
from app.tasks.document_pipeline import process_document_pipeline
from app.utils.dependencies import (
    get_current_tenant_user,
    get_db,
    require_user_or_chat_consumer,
)
from app.utils.response import success_response
from app.utils.s3 import S3Manager

router = APIRouter()

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
    current_user: User = Depends(get_current_tenant_user),
):
    """
    Upload a document for processing and embedding generation.

    Supported file types: PDF (.pdf), Markdown (.md), Text (.txt, .text), DOCX (.docx), DOC (.doc)
    Maximum file size: Configured in settings (default 50MB)

    The document will be:
    1. Uploaded to S3 storage
    2. Queued for background processing
    3. Text extracted and chunked
    4. Embeddings generated
    5. Stored in vector database
    """
    # Validate file extension
    file_extension = os.path.splitext(file.filename)[1].lower()
    if file_extension not in settings.ALLOWED_DOCUMENT_TYPES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"File type {file_extension} not allowed. Supported types: {', '.join(settings.ALLOWED_DOCUMENT_TYPES)}",
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
    max_size = settings.MAX_FILE_SIZE_MB * 1024 * 1024
    if file_size > max_size:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"File size exceeds {settings.MAX_FILE_SIZE_MB}MB limit",
        )

    # Get tenant_id from user
    tenant_id = current_user.tenant_id
    if not tenant_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="User does not have an associated tenant",
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


@router.post(
    "/scrape",
    response_model=DocumentScrapeResponse,
    status_code=status.HTTP_201_CREATED,
)
async def scrape_document(
    scrape_request: DocumentScrapeRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_tenant_user),
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

    # Get tenant_id from user
    tenant_id = current_user.tenant_id
    if not tenant_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="User does not have an associated tenant",
        )

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
    current_user: User = Depends(get_current_tenant_user),
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

    # Get tenant_id from user
    tenant_id = current_user.tenant_id
    if not tenant_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="User does not have an associated tenant",
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
            # Validate file extension
            file_extension = os.path.splitext(file.filename)[1].lower()
            if file_extension not in settings.ALLOWED_DOCUMENT_TYPES:
                failed_uploads.append(
                    {
                        "filename": file.filename,
                        "error": f"File type {file_extension} not allowed. Supported types: {', '.join(settings.ALLOWED_DOCUMENT_TYPES)}",
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
            max_size = settings.MAX_FILE_SIZE_MB * 1024 * 1024
            if file_size > max_size:
                failed_uploads.append(
                    {
                        "filename": file.filename,
                        "error": f"File size exceeds {settings.MAX_FILE_SIZE_MB}MB limit",
                    }
                )
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
    current_user: User = Depends(get_current_tenant_user),
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
    tenant_id = current_user.tenant_id
    if not tenant_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="User does not have an associated tenant",
        )

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
    current_user: User = Depends(get_current_tenant_user),
):
    """
    Get detailed information about a specific document.
    """
    tenant_id = current_user.tenant_id
    if not tenant_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="User does not have an associated tenant",
        )

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
    if isinstance(current_user_or_consumer, ChatConsumer):
        tenant_id = current_user_or_consumer.tenant_id
    else:
        tenant_id = current_user_or_consumer.tenant_id
    if not tenant_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="User does not have an associated tenant",
        )

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

    if not document.s3_url:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Document does not have an associated S3 URL",
        )

    prefix = f"s3://{settings.S3_BUCKET_NAME}/"
    if not document.s3_url.startswith(prefix):
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Invalid S3 URL stored for document",
        )

    s3_key = document.s3_url[len(prefix) :]

    try:
        s3_manager = S3Manager(
            bucket_name=settings.S3_BUCKET_NAME,
            region_name=settings.S3_REGION,
            aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
            aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
        )
        # Determine MIME type from document extension for better inline rendering
        ext = (document.document_type or "").lower()
        mime_map = {
            ".pdf": "application/pdf",
            ".md": "text/markdown; charset=utf-8",
            ".txt": "text/plain; charset=utf-8",
            ".doc": "application/msword",
            ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            ".xls": "application/vnd.ms-excel",
            ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        }
        content_type = mime_map.get(ext, "application/octet-stream")

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
    current_user: User = Depends(get_current_tenant_user),
):
    """
    Delete multiple documents and associated data.
    """
    tenant_id = current_user.tenant_id
    if not tenant_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="User does not have an associated tenant",
        )

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
    current_user: User = Depends(get_current_tenant_user),
):
    """
    Delete a document and all associated data.

    This will:
    1. Delete the file from S3
    2. Delete embeddings from vector store
    3. Delete database records (document and processing jobs)
    """
    tenant_id = current_user.tenant_id
    if not tenant_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="User does not have an associated tenant",
        )

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
    current_user: User = Depends(get_current_tenant_user),
):
    """
    Perform semantic search across documents.

    Uses embeddings to find relevant document chunks based on the query.
    Results are ranked by semantic similarity (distance).
    """
    tenant_id = current_user.tenant_id
    if not tenant_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="User does not have an associated tenant",
        )

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
    current_user: User = Depends(get_current_tenant_user),
):
    """
    Get all processing jobs for a document.

    Useful for debugging and tracking document processing history.
    """
    tenant_id = current_user.tenant_id
    if not tenant_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="User does not have an associated tenant",
        )

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
    current_user: User = Depends(get_current_tenant_user),
):
    """
    Get all chunks for a specific document.

    Useful for:
    - Inspecting chunk quality
    - Debugging chunking issues
    - Validating chunk size and overlap
    - Reviewing chunk content before RAG queries
    """
    tenant_id = current_user.tenant_id
    if not tenant_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="User does not have an associated tenant",
        )

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
    current_user: User = Depends(get_current_tenant_user),
):
    """
    Trigger reprocessing of a document.

    Useful when:
    - Previous processing failed
    - Tenant configuration changed (chunk size, embedding model, etc.)
    - Document needs to be re-indexed
    """
    tenant_id = current_user.tenant_id
    if not tenant_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="User does not have an associated tenant",
        )

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

    # Queue background processing with Celery Pipeline
    process_document_pipeline.delay(document.id, job.id)

    return success_response(
        data={
            "message": "Document queued for reprocessing",
            "document_id": document.id,
            "job_id": job.id,
        },
        status_code=status.HTTP_202_ACCEPTED,
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


@router.get("/text/{doc_uuid}")
async def get_document_text(
    doc_uuid: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_tenant_user),
):
    """
    Return the full extracted text of a document by its UUID (doc_id).
    Fetches chunks from Qdrant, sorts by chunk_index, and joins them.
    Used by the heatmap citation viewer to display the full document.
    """
    tenant_id = current_user.tenant_id
    if not tenant_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="User does not have an associated tenant",
        )

    result = await db.execute(
        select(Document).where(
            Document.doc_id == doc_uuid,
            Document.tenant_id == tenant_id,
        )
    )
    document = result.scalar_one_or_none()
    if not document:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Document not found",
        )

    chunks = await get_document_service().vector_store.get_document_chunks(
        document_id=doc_uuid,
        tenant_id=tenant_id,
    )

    chunks.sort(key=lambda c: c.get("metadata", {}).get("chunk_index", 0))
    full_text = "\n\n".join(c.get("text", "") for c in chunks)

    return success_response(
        data={"title": document.name, "text": full_text}
    )
