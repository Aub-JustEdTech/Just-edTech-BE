"""
Upload batch model for tracking bulk document uploads.
"""

import enum

from sqlalchemy import (
    BigInteger,
    Column,
    ForeignKey,
    Integer,
    String,
    Text,
    TypeDecorator,
)
from sqlalchemy.dialects.postgresql import ENUM as PG_ENUM
from sqlalchemy.orm import relationship

from app.models.base import BaseModel


class BatchStatus(str, enum.Enum):
    """Batch processing status"""

    PENDING = "pending"  # Batch created, documents being uploaded
    PROCESSING = "processing"  # At least one document is processing
    COMPLETED = "completed"  # All documents completed successfully
    PARTIAL_SUCCESS = "partial_success"  # Some succeeded, some failed
    FAILED = "failed"  # All documents failed


# PostgreSQL enum type matching the database schema
_batch_status_enum = PG_ENUM(
    "pending",
    "processing",
    "completed",
    "partial_success",
    "failed",
    name="batchstatus",
    create_type=False,  # Use existing type, don't create
)


class BatchStatusType(TypeDecorator):
    """Type decorator to ensure enum values are properly serialized"""

    impl = _batch_status_enum
    cache_ok = True

    def process_bind_param(self, value, dialect):
        """Convert enum object to its value when binding to database"""
        if value is None:
            return None
        if isinstance(value, BatchStatus):
            return value.value
        return value

    def process_result_value(self, value, dialect):
        """Convert database value back to enum object when reading"""
        if value is None:
            return None
        return BatchStatus(value)


batch_status_enum = BatchStatusType()


class UploadBatch(BaseModel):
    """
    Tracks a batch of documents uploaded together.

    Allows efficient status tracking for bulk uploads without
    making N API calls (one per document).

    Example: Upload 200 documents
    - 1 UploadBatch created with batch_id
    - 200 Documents linked to this batch
    - Frontend polls: GET /batches/{batch_id}/status (1 API call)
    - Returns: {total: 200, completed: 150, processing: 40, failed: 10}
    """

    __tablename__ = "upload_batches"

    batch_id = Column(String, nullable=False, unique=True, index=True)
    tenant_id = Column(
        BigInteger,
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Batch metadata
    total_documents = Column(Integer, default=0, nullable=False)
    completed_documents = Column(Integer, default=0, nullable=False)
    failed_documents = Column(Integer, default=0, nullable=False)
    processing_documents = Column(Integer, default=0, nullable=False)
    pending_documents = Column(Integer, default=0, nullable=False)

    status = Column(
        batch_status_enum, default=BatchStatus.PENDING, nullable=False, index=True
    )

    # Optional: User-provided description
    description = Column(Text, nullable=True)
    error_summary = Column(Text, nullable=True)

    # Relationships
    tenant = relationship("Tenant", backref="upload_batches")
    documents = relationship("Document", back_populates="upload_batch")

    def update_status(self):
        """Calculate and update batch status based on document statuses."""
        if self.total_documents == 0:
            self.status = BatchStatus.PENDING
            return

        if self.completed_documents == self.total_documents:
            self.status = BatchStatus.COMPLETED
        elif self.failed_documents == self.total_documents:
            self.status = BatchStatus.FAILED
        elif self.completed_documents > 0 or self.failed_documents > 0:
            if self.processing_documents > 0 or self.pending_documents > 0:
                self.status = BatchStatus.PROCESSING
            else:
                # All done, but mixed results
                self.status = BatchStatus.PARTIAL_SUCCESS
        else:
            self.status = BatchStatus.PROCESSING

    @property
    def progress_percentage(self) -> float:
        """Calculate completion percentage."""
        if self.total_documents == 0:
            return 0.0
        completed = self.completed_documents + self.failed_documents
        return round((completed / self.total_documents) * 100, 2)
