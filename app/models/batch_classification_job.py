"""
OpenAI Batch API job tracking.

One row per submitted Batch classification job. The pending_classifications
table holds the per-chunk queue; this table tracks the lifecycle of one
batch submission (input JSONL → OpenAI → output JSONL → applied to Qdrant
and heatmap_aggregate).
"""

from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, Index, Integer, String, Text

from app.models.base import BaseModel


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class BatchClassificationJob(BaseModel):
    """Tracks one OpenAI Batch API submission for chunk classification."""

    __tablename__ = "batch_classification_jobs"
    __table_args__ = (
        Index("ix_batch_classification_jobs_status", "status"),
    )

    # OpenAI batch id (e.g. "batch_abc123"). PK is the auto id from
    # BaseModel; this is a separate unique business key.
    batch_id = Column(String(64), nullable=False, unique=True, index=True)
    input_jsonl_s3_key = Column(Text, nullable=False)
    output_jsonl_s3_key = Column(Text, nullable=True)

    chunk_count = Column(Integer, nullable=False, default=0)
    # submitted | in_progress | completed | failed | expired | applied
    status = Column(String(16), nullable=False, default="submitted")

    submitted_at = Column(DateTime(timezone=True), nullable=False, default=_utcnow)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    applied_at = Column(DateTime(timezone=True), nullable=True)

    error_message = Column(Text, nullable=True)
