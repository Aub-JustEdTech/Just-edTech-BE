"""
Document processing job model for async job tracking.
"""

import enum

from sqlalchemy import (
    BigInteger,
    Column,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    TypeDecorator,
)
from sqlalchemy.dialects.postgresql import ENUM as PG_ENUM
from sqlalchemy.orm import relationship

from app.models.base import BaseModel


class JobStatus(str, enum.Enum):
    """Processing job status"""

    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


# PostgreSQL enum type matching the database schema
_job_status_enum = PG_ENUM(
    "pending",
    "processing",
    "completed",
    "failed",
    name="jobstatus",
    create_type=False,  # Use existing type, don't create
)


class JobStatusType(TypeDecorator):
    """Type decorator to ensure enum values are properly serialized"""

    impl = _job_status_enum
    cache_ok = True

    def process_bind_param(self, value, dialect):
        """Convert enum object to its value when binding to database"""
        if value is None:
            return None
        if isinstance(value, JobStatus):
            return value.value
        return value

    def process_result_value(self, value, dialect):
        """Convert database value back to enum object when reading"""
        if value is None:
            return None
        return JobStatus(value)


job_status_enum = JobStatusType()


class DocumentProcessingJob(BaseModel):
    """Tracks document processing jobs for async operations"""

    __tablename__ = "document_processing_jobs"

    document_id = Column(
        BigInteger,
        ForeignKey("documents.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    status = Column(
        job_status_enum, default=JobStatus.PENDING, nullable=False, index=True
    )
    processor_type = Column(String, nullable=False)  # pdf, md, docx, txt
    error_message = Column(Text, nullable=True)
    chunks_created = Column(Integer, default=0, nullable=False)
    processing_time_seconds = Column(Float, nullable=True)

    # Relationships
    document = relationship("Document", back_populates="processing_jobs")
    processing_stages = relationship(
        "DocumentProcessingStage", back_populates="job", cascade="all, delete-orphan"
    )
