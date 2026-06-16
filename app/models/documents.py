"""
Document model aligned with ERD.
Enhanced for document ingestion and processing.
"""

import enum

from sqlalchemy import BigInteger, Column, ForeignKey, Integer, String, Text, TypeDecorator
from sqlalchemy.dialects.postgresql import ENUM as PG_ENUM, JSONB
from sqlalchemy.orm import relationship

from app.models.base import BaseModel


class ProcessingStatus(str, enum.Enum):
    """Document processing status"""

    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


# PostgreSQL enum type matching the database schema
_processing_status_enum = PG_ENUM(
    "pending",
    "processing",
    "completed",
    "failed",
    name="processingstatus",
    create_type=False,  # Use existing type, don't create
)


class ProcessingStatusType(TypeDecorator):
    """Type decorator to ensure enum values are properly serialized"""

    impl = _processing_status_enum
    cache_ok = True

    def process_bind_param(self, value, dialect):
        """Convert enum object to its value when binding to database"""
        if value is None:
            return None
        if isinstance(value, ProcessingStatus):
            return value.value
        return value

    def process_result_value(self, value, dialect):
        """Convert database value back to enum object when reading"""
        if value is None:
            return None
        return ProcessingStatus(value)


processing_status_enum = ProcessingStatusType()


class Document(BaseModel):
    """Document per ERD with processing enhancements."""

    __tablename__ = "documents"

    name = Column(String, nullable=False)
    doc_id = Column(String, nullable=False, unique=True, index=True)
    s3_url = Column(String, nullable=True, unique=True, index=True)

    # Enhanced fields for processing
    tenant_id = Column(
        BigInteger,
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    document_type = Column(String, nullable=False)  # pdf, md, docx, txt
    processing_status = Column(
        processing_status_enum,
        default=ProcessingStatus.PENDING,
        nullable=False,
        index=True,
    )
    chunk_count = Column(Integer, default=0, nullable=False)
    file_size_bytes = Column(Integer, nullable=True)
    error_message = Column(String, nullable=True)

    # Document intelligence (populated by summarizer pipeline stage)
    summary = Column(Text, nullable=True)
    doc_category = Column(String(100), nullable=True, index=True)
    doc_date_range = Column(String(100), nullable=True)

    # Source tracking (populated by Box sync or other ingestors)
    source_id = Column(String(255), nullable=True, index=True)
    source_type = Column(String(50), nullable=True, index=True)
    source_metadata = Column(JSONB, nullable=True)

    # Batch upload tracking (optional - for bulk uploads)
    upload_batch_id = Column(
        BigInteger,
        ForeignKey("upload_batches.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    # Relationships
    tenant = relationship("Tenant", back_populates="documents")
    conversations = relationship(
        "Conversation",
        secondary="conversation_documents",
        back_populates="documents",
    )
    processing_jobs = relationship(
        "DocumentProcessingJob", back_populates="document", cascade="all, delete-orphan"
    )
    processing_stages = relationship(
        "DocumentProcessingStage",
        back_populates="document",
        cascade="all, delete-orphan",
    )
    upload_batch = relationship("UploadBatch", back_populates="documents")
