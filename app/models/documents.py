"""
Document model aligned with ERD.
Enhanced for document ingestion and processing.
"""

import enum

from sqlalchemy import BigInteger, Column, Date, ForeignKey, Integer, String, Text, TypeDecorator
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

    # Heatmap-ingest extensions (populated by step2_6_classify_document for
    # school_scraper docs). meeting_date promotes the source_metadata JSONB
    # value to a real column so we can index/filter on it.
    content_hash = Column(String(64), nullable=True, index=True)
    entity_type = Column(String(64), nullable=True, index=True)
    doc_kind = Column(String(64), nullable=True)
    meeting_date = Column(Date, nullable=True, index=True)

    # Heatmap-ingest V1 metadata (see plan: Heatmap Ingest Metadata v1).
    # Doc-level denormalized fields propagated from School + source_metadata
    # + DocClassifier output, also mirrored onto every Qdrant chunk payload
    # for payload pre-filtering and facet exploration.
    state = Column(String(2), nullable=True, index=True)
    district_name = Column(String(512), nullable=True, index=True)
    school_year = Column(String(9), nullable=True, index=True)  # e.g. "2023-2024"
    quarter_month = Column(String(7), nullable=True, index=True)  # e.g. "2024-03"
    # Distinct from the file-extension document_type above. Populated by
    # DocClassifier with one of the meeting_doc_type enum values (Minutes,
    # Agenda, Agenda Attachment, Public Comment Transcript, Policy Document,
    # Presentation Slide).
    meeting_doc_type = Column(String(64), nullable=True, index=True)
    meeting_body = Column(String(128), nullable=True, index=True)
    # V1: default clean_digital for all docs. Real OCR-confidence detection
    # is deferred; the field is in place so the schema is forward-compatible.
    document_quality = Column(
        String(32), nullable=False, server_default="clean_digital", index=True
    )

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
