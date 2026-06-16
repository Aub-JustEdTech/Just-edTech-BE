"""
Schemas for upload batch tracking.
"""

from datetime import datetime

from pydantic import BaseModel, Field

from app.models.upload_batches import BatchStatus


class BatchStatusSummary(BaseModel):
    """Summary of batch processing status."""

    total: int = Field(..., description="Total number of documents in batch")
    pending: int = Field(..., description="Number of pending documents")
    processing: int = Field(..., description="Number of processing documents")
    completed: int = Field(..., description="Number of completed documents")
    failed: int = Field(..., description="Number of failed documents")
    progress_percentage: float = Field(..., description="Completion percentage (0-100)")


class DocumentStatusInBatch(BaseModel):
    """Individual document status within a batch."""

    id: int
    name: str
    doc_id: str
    document_type: str
    processing_status: str
    error_message: str | None = None
    chunk_count: int = 0
    file_size_bytes: int | None = None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class UploadBatchResponse(BaseModel):
    """Response for upload batch creation and retrieval."""

    id: int
    batch_id: str
    tenant_id: int
    status: BatchStatus
    total_documents: int
    completed_documents: int
    failed_documents: int
    processing_documents: int
    pending_documents: int
    progress_percentage: float
    description: str | None = None
    error_summary: str | None = None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class UploadBatchDetailResponse(UploadBatchResponse):
    """Detailed batch response including all documents."""

    documents: list[DocumentStatusInBatch]


class CreateBatchRequest(BaseModel):
    """Request to create a new upload batch."""

    description: str | None = Field(
        None,
        description="Optional description for this batch (e.g., 'Q4 2024 Reports')",
    )


class BatchStatusResponse(BaseModel):
    """Real-time status response for batch monitoring."""

    batch_id: str
    status: BatchStatus
    summary: BatchStatusSummary
    estimated_time_remaining_seconds: float | None = None
    error_summary: str | None = None
