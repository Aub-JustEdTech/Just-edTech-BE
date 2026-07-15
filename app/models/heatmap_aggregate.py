"""
Per-(school, topic) heatmap aggregate.

Updated incrementally as batch classification results are applied. The
heatmap endpoint reads this table for the summary view; citation drill-down
hits Qdrant filtered by source_id + topic.

Note: `source_id` here is the schools.id (BigInteger) — NOT documents.source_id
(string). The heatmap is one row per (school, topic); we count chunks/docs/
meetings that hit the topic for that school.
"""

from datetime import date

from sqlalchemy import BigInteger, Column, Date, ForeignKey, Index, Integer, String
from sqlalchemy.dialects.postgresql import JSONB

from app.models.base import BaseModel


class HeatmapAggregate(BaseModel):
    """Precomputed per-school, per-topic aggregate backing the heatmap map."""

    __tablename__ = "heatmap_aggregate"
    __table_args__ = (
        # Composite PK via a single PK constraint declared below.
        Index("ix_heatmap_aggregate_topic", "topic"),
    )

    source_id = Column(
        BigInteger,
        ForeignKey("schools.id", ondelete="CASCADE"),
        primary_key=True,
    )
    topic = Column(String(64), primary_key=True)

    chunk_count = Column(Integer, nullable=False, default=0)
    doc_count = Column(Integer, nullable=False, default=0)
    meeting_count = Column(Integer, nullable=False, default=0)
    last_meeting_date = Column(Date, nullable=True)

    # Per-action-type counts, e.g. {"instruction_reduced": 3, "book_challenged": 2}.
    # Stored as JSONB so adding an action_type later doesn't require a migration.
    action_types = Column(JSONB, nullable=False, default=dict)
