"""Media transcription usage — one row per transcription attempt that could cost money.

Written as an event log rather than a rolling counter, because the quota check
and the audit answer different questions: "may this tenant spend more this
month" needs a SUM, but "why was this tenant billed" needs the individual
items. A counter cannot answer the second, and cannot be rebuilt if it drifts.

Free transcriptions are recorded too, with ``billable=False``. A YouTube video
that had captions cost nothing, but knowing it was ingested for free is what
explains a low bill against high usage.
"""

from sqlalchemy import (
    DECIMAL,
    BigInteger,
    Boolean,
    Column,
    Date,
    ForeignKey,
    Index,
    Integer,
    String,
)
from sqlalchemy.orm import relationship

from app.models.base import BaseModel

# source values
SOURCE_UPLOAD = "upload"
SOURCE_YOUTUBE = "youtube"
SOURCE_URL = "url"
SOURCE_SCHOOL_SCRAPER = "school_scraper"


class MediaTranscriptionUsage(BaseModel):
    """One transcription of one media item, billable or not."""

    __tablename__ = "media_transcription_usage"

    tenant_id = Column(
        BigInteger,
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # Nullable: usage is recorded even when the document is later deleted, and
    # SET NULL keeps the spend record rather than erasing the evidence.
    document_id = Column(
        BigInteger,
        ForeignKey("documents.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    # upload | youtube | url | school_scraper
    source = Column(String(32), nullable=False, index=True)
    # assemblyai | youtube_captions
    provider = Column(String(64), nullable=True)
    speech_model = Column(String(64), nullable=True)

    duration_seconds = Column(Integer, nullable=False, default=0)
    # False for YouTube captions, which are always free. Only billable rows
    # count against the monthly cap.
    billable = Column(Boolean, nullable=False, default=True, index=True)
    estimated_cost_usd = Column(DECIMAL(12, 6), nullable=True, default=0)

    # Denormalised month key ("YYYY-MM-01"). The quota query runs on every
    # media ingest, and a plain indexed column beats date_trunc() over a
    # growing table.
    usage_month = Column(Date, nullable=False, index=True)

    tenant = relationship("Tenant")

    __table_args__ = (
        Index(
            "idx_media_usage_quota",
            "tenant_id",
            "usage_month",
            "billable",
        ),
    )

    def __repr__(self):
        return (
            f"<MediaTranscriptionUsage(tenant_id={self.tenant_id}, "
            f"source={self.source}, duration={self.duration_seconds}s, "
            f"billable={self.billable})>"
        )
