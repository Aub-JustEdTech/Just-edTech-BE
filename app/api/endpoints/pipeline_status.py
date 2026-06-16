"""
Pipeline Status Monitoring Endpoints
Provides real-time visibility into document processing pipeline.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.documents import Document
from app.models.processing_stages import DocumentProcessingStage
from app.schemas.users import User
from app.utils.dependencies import get_current_tenant_user, get_db
from app.utils.redis_pipeline import get_redis_tracker
from app.utils.response import success_response

router = APIRouter(prefix="/pipeline")


@router.get("/{document_id}/status")
async def get_pipeline_status(
    document_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_tenant_user),
):
    """
    Get detailed pipeline status for a document.

    Returns:
    - Current stage
    - Progress percentage
    - Status of each stage (downloading, extracting, chunking, embedding, storing)
    - Error history (if any)
    - Timing information

    Example Response:
    ```json
    {
        "document_id": 123,
        "document_name": "example.pdf",
        "overall_status": "processing",
        "current_stage": "embedding",
        "progress_percentage": 65.5,
        "stages": {
            "downloading": {
                "status": "completed",
                "started_at": "2024-01-01T10:00:00Z",
                "completed_at": "2024-01-01T10:00:05Z",
                "duration_seconds": 5.2,
                "input_size": 1048576,
                "output_size": 1048576
            },
            "extracting": {
                "status": "completed",
                "started_at": "2024-01-01T10:00:05Z",
                "completed_at": "2024-01-01T10:00:15Z",
                "duration_seconds": 10.1,
                "input_size": 1048576,
                "output_size": 125000
            },
            "chunking": {
                "status": "completed",
                "started_at": "2024-01-01T10:00:15Z",
                "completed_at": "2024-01-01T10:00:18Z",
                "duration_seconds": 3.4,
                "input_size": 125000,
                "output_size": 45
            },
            "embedding": {
                "status": "in_progress",
                "started_at": "2024-01-01T10:00:18Z",
                "completed_at": null,
                "duration_seconds": null,
                "input_size": 45,
                "output_size": null
            },
            "storing": {
                "status": "pending",
                "started_at": null,
                "completed_at": null,
                "duration_seconds": null,
                "input_size": null,
                "output_size": null
            }
        },
        "errors": [],
        "last_updated": "2024-01-01T10:05:30.123456"
    }
    ```
    """
    # Verify document belongs to user's tenant
    document = await db.get(Document, document_id)
    if not document:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Document not found",
        )

    if document.tenant_id != current_user.tenant_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied",
        )

    # Get Redis status (fast, real-time)
    redis_tracker = get_redis_tracker()
    redis_status = redis_tracker.get_document_status(document_id)
    redis_stages = redis_tracker.get_all_stages(document_id)
    redis_errors = redis_tracker.get_errors(document_id)

    # Get PostgreSQL status (durable, detailed)
    result = await db.execute(
        select(DocumentProcessingStage)
        .where(DocumentProcessingStage.document_id == document_id)
        .order_by(DocumentProcessingStage.created_at)
    )
    pg_stages = result.scalars().all()

    # Build response
    stages_detail = {}
    for stage_name in ["downloading", "extracting", "chunking", "embedding", "storing"]:
        # Find matching PostgreSQL stage record
        pg_stage = next((s for s in pg_stages if s.stage.value == stage_name), None)

        # Get Redis stage status (more up-to-date)
        redis_stage_status = redis_stages.get(stage_name, "pending")

        if pg_stage:
            stages_detail[stage_name] = {
                "status": redis_stage_status or pg_stage.status.value,
                "started_at": pg_stage.started_at.isoformat()
                if pg_stage.started_at
                else None,
                "completed_at": pg_stage.completed_at.isoformat()
                if pg_stage.completed_at
                else None,
                "duration_seconds": pg_stage.duration_seconds,
                "input_size": pg_stage.input_size,
                "output_size": pg_stage.output_size,
                "error_message": pg_stage.error_message,
            }
        else:
            stages_detail[stage_name] = {
                "status": redis_stage_status,
                "started_at": None,
                "completed_at": None,
                "duration_seconds": None,
                "input_size": None,
                "output_size": None,
                "error_message": None,
            }

    # Determine overall status
    overall_status = document.processing_status.value
    current_stage = (
        redis_status.get("current_stage", "pending") if redis_status else "pending"
    )
    progress = (
        float(redis_status.get("progress_percentage", 0.0)) if redis_status else 0.0
    )

    return success_response(
        data={
            "document_id": document_id,
            "document_name": document.name,
            "overall_status": overall_status,
            "current_stage": current_stage,
            "progress_percentage": progress,
            "stages": stages_detail,
            "errors": redis_errors,
            "last_updated": redis_status.get("last_updated") if redis_status else None,
        }
    )


@router.get("/active-jobs")
async def get_active_jobs(
    limit: int = 100,
    current_user: User = Depends(get_current_tenant_user),
):
    """
    Get list of currently active processing jobs.

    Returns:
    - List of document IDs currently being processed
    - Count of active jobs

    Example Response:
    ```json
    {
        "active_job_count": 15,
        "active_jobs": ["123", "124", "125", ...]
    }
    ```
    """
    redis_tracker = get_redis_tracker()

    active_jobs = redis_tracker.get_active_jobs(limit=limit)
    count = redis_tracker.get_active_job_count()

    return success_response(
        data={
            "active_job_count": count,
            "active_jobs": active_jobs,
        }
    )


@router.get("/batch/{batch_id}/progress")
async def get_batch_progress(
    batch_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_tenant_user),
):
    """
    Get aggregated progress for a batch upload.

    Returns:
    - Total documents
    - Stage breakdown (how many docs in each stage)
    - Progress percentage
    - Failed documents with error details

    Example Response:
    ```json
    {
        "batch_id": 456,
        "total_documents": 100,
        "completed": 75,
        "failed": 5,
        "in_progress": 20,
        "progress_percentage": 75.0,
        "stage_breakdown": {
            "downloading": 5,
            "extracting": 10,
            "chunking": 15,
            "embedding": 20,
            "storing": 25,
            "completed": 20,
            "failed": 5
        },
        "failed_documents": [
            {
                "document_id": 789,
                "document_name": "failed.pdf",
                "failed_at_stage": "embedding",
                "error": "OpenAI API rate limit exceeded",
                "retry_count": 3
            }
        ]
    }
    ```
    """
    # TODO: Verify batch belongs to user's tenant

    redis_tracker = get_redis_tracker()
    progress = redis_tracker.get_batch_progress(batch_id)

    if not progress:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Batch progress not found (may have expired or not started)",
        )

    # Get failed documents from PostgreSQL
    result = await db.execute(
        select(Document).where(
            Document.upload_batch_id == batch_id, Document.processing_status == "failed"
        )
    )
    failed_docs = result.scalars().all()

    failed_documents = []
    for doc in failed_docs:
        # Get error details from Redis
        errors = redis_tracker.get_errors(doc.id)
        latest_error = errors[0] if errors else None

        failed_documents.append(
            {
                "document_id": doc.id,
                "document_name": doc.name,
                "failed_at_stage": latest_error.get("stage")
                if latest_error
                else "unknown",
                "error": latest_error.get("error")
                if latest_error
                else doc.error_message,
                "retry_count": latest_error.get("retry", 0) if latest_error else 0,
            }
        )

    return success_response(
        data={
            "batch_id": batch_id,
            "total_documents": progress.get("total", 0),
            "completed": progress.get("completed", 0),
            "failed": progress.get("failed", 0),
            "in_progress": progress.get("in_progress", 0),
            "progress_percentage": (
                (progress.get("completed", 0) / progress.get("total", 1)) * 100
                if progress.get("total", 0) > 0
                else 0.0
            ),
            "stage_breakdown": {
                "downloading": progress.get("downloading", 0),
                "extracting": progress.get("extracting", 0),
                "chunking": progress.get("chunking", 0),
                "embedding": progress.get("embedding", 0),
                "storing": progress.get("storing", 0),
                "completed": progress.get("completed", 0),
                "failed": progress.get("failed", 0),
            },
            "failed_documents": failed_documents,
        }
    )


@router.post("/batch-status")
async def get_batch_status(
    document_ids: list[int],
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_tenant_user),
):
    """
    Get status for multiple documents in a single API call.

    **This is the recommended way to monitor multiple documents.**

    Request Body:
    ```json
    {
        "document_ids": [123, 124, 125, ...]
    }
    ```

    Response:
    ```json
    {
        "total_documents": 100,
        "summary": {
            "pending": 5,
            "processing": 30,
            "completed": 60,
            "failed": 5
        },
        "stage_breakdown": {
            "downloading": 5,
            "extracting": 10,
            "chunking": 8,
            "embedding": 7,
            "storing": 0,
            "completed": 60,
            "failed": 5
        },
        "documents": [
            {
                "document_id": 123,
                "document_name": "doc1.pdf",
                "overall_status": "processing",
                "current_stage": "embedding",
                "progress_percentage": 65.5,
                "last_updated": "2024-01-01T10:05:30Z"
            },
            ...
        ]
    }
    ```

    **Usage**:
    - Poll this endpoint every 2-5 seconds
    - Single API call for all documents
    - Much faster than individual calls
    """
    if not document_ids:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="document_ids list cannot be empty",
        )

    if len(document_ids) > 1000:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Maximum 1000 document IDs allowed per request",
        )

    # Verify all documents belong to user's tenant
    result = await db.execute(
        select(Document).where(
            Document.id.in_(document_ids), Document.tenant_id == current_user.tenant_id
        )
    )
    documents = result.scalars().all()

    if len(documents) != len(document_ids):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Some documents not found or access denied",
        )

    # Get Redis status for all documents (fast!)
    redis_tracker = get_redis_tracker()

    documents_status = []
    summary = {
        "pending": 0,
        "processing": 0,
        "completed": 0,
        "failed": 0,
    }
    stage_breakdown = {
        "downloading": 0,
        "extracting": 0,
        "chunking": 0,
        "embedding": 0,
        "storing": 0,
        "completed": 0,
        "failed": 0,
    }

    for doc in documents:
        # Get Redis status (fast)
        redis_status = redis_tracker.get_document_status(doc.id)

        if redis_status:
            current_stage = redis_status.get("current_stage", "pending")
            progress = float(redis_status.get("progress_percentage", 0.0))
            last_updated = redis_status.get("last_updated")
            overall_status = redis_status.get("status", doc.processing_status.value)
        else:
            # Fallback to PostgreSQL if Redis data expired
            current_stage = "pending"
            progress = 0.0
            last_updated = None
            overall_status = doc.processing_status.value

        documents_status.append(
            {
                "document_id": doc.id,
                "document_name": doc.name,
                "overall_status": overall_status,
                "current_stage": current_stage,
                "progress_percentage": progress,
                "last_updated": last_updated,
            }
        )

        # Update summary
        if overall_status == "completed":
            summary["completed"] += 1
            stage_breakdown["completed"] += 1
        elif overall_status == "failed":
            summary["failed"] += 1
            stage_breakdown["failed"] += 1
        elif overall_status in ["processing", "in_progress"]:
            summary["processing"] += 1
            # Update stage breakdown
            if current_stage in stage_breakdown:
                stage_breakdown[current_stage] += 1
        else:
            summary["pending"] += 1

    return success_response(
        data={
            "total_documents": len(documents),
            "summary": summary,
            "stage_breakdown": stage_breakdown,
            "documents": documents_status,
        }
    )


@router.get("/health")
async def pipeline_health():
    """
    Check pipeline health (Redis connection, active jobs, etc.).

    Example Response:
    ```json
    {
        "status": "healthy",
        "redis_connected": true,
        "active_jobs": 15,
        "message": "Pipeline is operational"
    }
    ```
    """
    redis_tracker = get_redis_tracker()

    redis_healthy = redis_tracker.health_check()
    active_count = redis_tracker.get_active_job_count() if redis_healthy else 0

    if redis_healthy:
        return success_response(
            data={
                "status": "healthy",
                "redis_connected": True,
                "active_jobs": active_count,
                "message": "Pipeline is operational",
            }
        )
    else:
        return success_response(
            data={
                "status": "degraded",
                "redis_connected": False,
                "active_jobs": 0,
                "message": "Redis connection failed - pipeline status unavailable",
            }
        )
