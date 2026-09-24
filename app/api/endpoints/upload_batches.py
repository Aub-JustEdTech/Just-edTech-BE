"""
API endpoints for upload batch management.
"""


from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.crud import upload_batches as crud
from app.schemas.upload_batches import (
    BatchStatusResponse,
    BatchStatusSummary,
    CreateBatchRequest,
    DocumentStatusInBatch,
    UploadBatchDetailResponse,
    UploadBatchResponse,
)
from app.utils.dependencies import get_db, get_effective_tenant_id
from app.utils.response import success_response

router = APIRouter()


@router.post(
    "/",
    response_model=UploadBatchResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_batch(
    request: CreateBatchRequest,
    db: AsyncSession = Depends(get_db),
    tenant_id: int = Depends(get_effective_tenant_id),
):
    """
    Create a new upload batch for tracking bulk document uploads.

    **Usage:**
    1. Create a batch before uploading documents
    2. Upload documents with batch_id parameter
    3. Poll batch status with GET /batches/{batch_id}/status

    **Example:**
    ```python
    # Step 1: Create batch
    batch = create_batch(description="Q4 2024 Reports")

    # Step 2: Upload 200 documents with batch_id
    for file in files:
        upload_document(file, batch_id=batch.batch_id)

    # Step 3: Monitor progress (1 API call instead of 200)
    status = get_batch_status(batch.batch_id)
    # Returns: {completed: 150/200, progress: 75%}
    ```
    """
    batch = await crud.create_batch(
        db=db, tenant_id=tenant_id, description=request.description
    )

    # progress_percentage is automatically calculated as a property
    return success_response(
        data=UploadBatchResponse.model_validate(batch),
        status_code=status.HTTP_201_CREATED,
    )


@router.get("/", response_model=list[UploadBatchResponse])
async def list_batches(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
    tenant_id: int = Depends(get_effective_tenant_id),
):
    """
    List all upload batches for the current tenant.

    Ordered by creation date (newest first).
    """
    batches = await crud.list_batches(
        db=db, tenant_id=tenant_id, skip=skip, limit=limit
    )

    # progress_percentage is automatically calculated as a property for each batch
    response_data = [UploadBatchResponse.model_validate(batch) for batch in batches]
    extra = {"skip": skip, "limit": limit, "total": len(response_data)}
    return success_response(data=response_data, extra=extra)


@router.get("/{batch_id}", response_model=UploadBatchDetailResponse)
async def get_batch_detail(
    batch_id: str,
    db: AsyncSession = Depends(get_db),
    tenant_id: int = Depends(get_effective_tenant_id),
):
    """
    Get detailed information about a specific batch, including all documents.

    Use this to see the full list of documents in the batch.
    For real-time progress monitoring, use GET /batches/{batch_id}/status instead.
    """
    batch = await crud.get_batch(db=db, batch_id=batch_id, tenant_id=tenant_id)
    if not batch:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Batch not found",
        )

    # Get all documents in batch
    documents = await crud.get_batch_documents(
        db=db, batch_id=batch.id, tenant_id=tenant_id
    )

    # Build response (progress_percentage is automatically calculated)
    base_response = UploadBatchResponse.model_validate(batch)
    response_data = UploadBatchDetailResponse(
        **base_response.model_dump(),
        documents=[DocumentStatusInBatch.model_validate(doc) for doc in documents],
    )
    return success_response(data=response_data)


@router.get("/{batch_id}/status", response_model=BatchStatusResponse)
async def get_batch_status(
    batch_id: str,
    db: AsyncSession = Depends(get_db),
    tenant_id: int = Depends(get_effective_tenant_id),
):
    """
    Get real-time status of a batch upload.

    **Use this for frontend polling!**

    This endpoint is optimized for frequent polling:
    - Lightweight (doesn't return full document list)
    - Fast (uses aggregated counts)
    - Efficient (one API call for entire batch)

    **Frontend Example:**
    ```javascript
    // Poll every 2 seconds
    const interval = setInterval(async () => {
      const status = await fetch(`/batches/${batchId}/status`);
      const data = await status.json();

      updateProgressBar(data.summary.progress_percentage);
      console.log(`Progress: ${data.summary.completed}/${data.summary.total}`);

      if (data.status === 'completed' || data.status === 'partial_success') {
        clearInterval(interval);
        showResults(data);
      }
    }, 2000);
    ```

    **Response Example:**
    ```json
    {
      "batch_id": "uuid-123",
      "status": "processing",
      "summary": {
        "total": 200,
        "completed": 150,
        "processing": 40,
        "failed": 10,
        "pending": 0,
        "progress_percentage": 80.0
      }
    }
    ```
    """
    batch = await crud.get_batch(db=db, batch_id=batch_id, tenant_id=tenant_id)
    if not batch:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Batch not found",
        )

    # Refresh counts from database
    await crud.update_batch_counts(db=db, batch_id=batch.id)
    await db.refresh(batch)

    summary = BatchStatusSummary(
        total=batch.total_documents,
        pending=batch.pending_documents,
        processing=batch.processing_documents,
        completed=batch.completed_documents,
        failed=batch.failed_documents,
        progress_percentage=batch.progress_percentage,
    )

    # Estimate time remaining (simple calculation)
    estimated_time = None
    if batch.processing_documents > 0 and batch.completed_documents > 0:
        # Rough estimate: average 30 seconds per document
        estimated_time = batch.processing_documents * 30.0

    return success_response(
        data=BatchStatusResponse(
            batch_id=batch.batch_id,
            status=batch.status,
            summary=summary,
            estimated_time_remaining_seconds=estimated_time,
            error_summary=batch.error_summary,
        )
    )
