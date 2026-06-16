"""
Document Processing Stage Model
Tracks individual stages of document processing pipeline
"""

import enum

from sqlalchemy import (
    BigInteger,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    Text,
    TypeDecorator,
)
from sqlalchemy.dialects.postgresql import ENUM as PG_ENUM
from sqlalchemy.orm import relationship

from app.models.base import BaseModel


class ProcessingStage(str, enum.Enum):
    """Enum for document processing stages"""

    PENDING = "pending"
    DOWNLOADING = "downloading"
    EXTRACTING = "extracting"
    SUMMARIZING = "summarizing"
    CHUNKING = "chunking"
    EMBEDDING = "embedding"
    STORING = "storing"
    COMPLETED = "completed"
    FAILED = "failed"


class StageStatus(str, enum.Enum):
    """Enum for stage execution status"""

    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    RETRYING = "retrying"


# PostgreSQL enum types matching the database schema
_processing_stage_enum = PG_ENUM(
    "pending",
    "downloading",
    "extracting",
    "summarizing",
    "chunking",
    "embedding",
    "storing",
    "completed",
    "failed",
    name="processingstage",
    create_type=False,  # Use existing type, don't create
)

_stage_status_enum = PG_ENUM(
    "pending",
    "in_progress",
    "completed",
    "failed",
    "retrying",
    name="stagestatus",
    create_type=False,  # Use existing type, don't create
)


class ProcessingStageType(TypeDecorator):
    """Type decorator to ensure enum values are properly serialized"""

    impl = _processing_stage_enum
    cache_ok = True

    def process_bind_param(self, value, dialect):
        """Convert enum object to its value when binding to database"""
        if value is None:
            return None
        if isinstance(value, ProcessingStage):
            return value.value
        return value

    def process_result_value(self, value, dialect):
        """Convert database value back to enum object when reading"""
        if value is None:
            return None
        return ProcessingStage(value)


class StageStatusType(TypeDecorator):
    """Type decorator to ensure enum values are properly serialized"""

    impl = _stage_status_enum
    cache_ok = True

    def process_bind_param(self, value, dialect):
        """Convert enum object to its value when binding to database"""
        if value is None:
            return None
        if isinstance(value, StageStatus):
            return value.value
        return value

    def process_result_value(self, value, dialect):
        """Convert database value back to enum object when reading"""
        if value is None:
            return None
        return StageStatus(value)


processing_stage_enum = ProcessingStageType()
stage_status_enum = StageStatusType()


class DocumentProcessingStage(BaseModel):
    """
    Tracks individual stages of document processing.
    Each document goes through multiple stages, and this model tracks each one.

    Example flow:
    1. downloading -> in_progress -> completed
    2. extracting -> in_progress -> completed
    3. chunking -> in_progress -> completed
    4. embedding -> in_progress -> failed (retry) -> completed
    5. storing -> in_progress -> completed
    """

    __tablename__ = "document_processing_stages"

    # Foreign Keys
    document_id = Column(
        BigInteger,
        ForeignKey("documents.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    job_id = Column(
        BigInteger,
        ForeignKey("document_processing_jobs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Stage Information
    stage = Column(
        processing_stage_enum,
        nullable=False,
        index=True,
    )
    status = Column(
        stage_status_enum,
        nullable=False,
        default=StageStatus.PENDING,
        index=True,
    )

    # Timing Information
    started_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)
    duration_seconds = Column(Float, nullable=True)

    # Error Tracking
    error_message = Column(Text, nullable=True)
    error_traceback = Column(Text, nullable=True)
    retry_count = Column(Integer, default=0, nullable=False)

    # Metadata (for observability)
    input_size = Column(
        Integer, nullable=True
    )  # e.g., file size in bytes, number of chunks
    output_size = Column(
        Integer, nullable=True
    )  # e.g., number of chunks created, embeddings generated
    stage_metadata = Column(Text, nullable=True)  # JSON string for additional data

    # Relationships
    document = relationship("Document", back_populates="processing_stages")
    job = relationship("DocumentProcessingJob", back_populates="processing_stages")

    def __repr__(self):
        return f"<DocumentProcessingStage(doc_id={self.document_id}, stage={self.stage}, status={self.status})>"
