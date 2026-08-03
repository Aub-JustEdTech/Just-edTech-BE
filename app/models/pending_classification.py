"""
Per-chunk queue for the Batch API classification pipeline.

One row per chunk waiting to be classified (or already classified via a
batch). The ingest pipeline (step6_accumulate_batch) inserts rows here as
chunks are stored in Qdrant; the daily submit task pulls rows with
status='pending', builds a JSONL, submits to OpenAI Batch API, and flips
them to 'submitted' with a batch_id. The apply task flips them to 'applied'
once results are written to Qdrant and heatmap_aggregate.

We persist the chunk_text so the submit task can build JSONL without a
roundtrip to Qdrant, and so we can re-submit on failure without losing data.
"""

from datetime import date

from sqlalchemy import (
    BigInteger,
    Column,
    Date,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import UUID

from app.models.base import BaseModel


class PendingClassification(BaseModel):
    """A chunk awaiting (or post-) Batch API classification."""

    __tablename__ = "pending_classifications"
    __table_args__ = (
        Index("ix_pending_classifications_status", "status"),
        Index("ix_pending_classifications_batch", "batch_id"),
        Index("ix_pending_classifications_doc", "document_id"),
    )

    document_id = Column(
        BigInteger,
        ForeignKey("documents.id", ondelete="CASCADE"),
        nullable=False,
    )
    qdrant_point_id = Column(UUID(as_uuid=False), nullable=False)
    chunk_index = Column(Integer, nullable=False)

    chunk_text = Column(Text, nullable=False)
    page_number = Column(Integer, nullable=True)
    entity_type = Column(String(64), nullable=True)
    meeting_date = Column(Date, nullable=True)

    # FK to batch_classification_jobs.batch_id. Kept as plain String
    # (not FK) so a batch row can be deleted for cleanup without stranding
    # pending rows; the status field is the source of truth for state.
    batch_id = Column(String(64), nullable=True)
    # pending | submitted | applied | failed
    status = Column(String(16), nullable=False, default="pending")
    error_message = Column(Text, nullable=True)
