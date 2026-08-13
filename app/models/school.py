"""
School, scrape URL, and scraped media models.

These back the district-level school knowledge base: per-tenant MA school
districts (seeded from scripts/school_data/output/school_names.json), their
confirmed archive-page URLs, and per-media-item records that link into the
existing Document + vector store pipeline with content-hash dedup.
"""

from datetime import datetime, timezone

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship

from app.models.base import BaseModel


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class School(BaseModel):
    """
    A district-level school row.

    Seeded from scripts/school_data/output/school_names.json (396 MA
    districts). Scoped per-tenant via (tenant_id, org_code) uniqueness.
    `last_scrapped_at` is denormalized for fast FE display.
    """

    __tablename__ = "schools"
    __table_args__ = (
        UniqueConstraint("tenant_id", "org_code", name="uq_schools_tenant_org"),
    )

    tenant_id = Column(
        BigInteger,
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    org_code = Column(String(16), nullable=False, index=True)
    name = Column(String(512), nullable=False)
    district_type = Column(String(64), nullable=False)
    # 2-letter state abbreviation. Drives which vocabulary pack applies
    # during heatmap ingest classification (see vocabulary_packs.loader).
    # Seeded 'MA' for the initial 396-district corpus.
    state = Column(String(2), nullable=False, server_default="MA", index=True)
    website = Column(Text, nullable=True)
    last_scrapped_at = Column(DateTime(timezone=True), nullable=True)
    is_active = Column(Boolean, default=True, nullable=False)
    scrape_url_id = Column(
        BigInteger,
        ForeignKey("school_scrape_urls.id", ondelete="SET NULL"),
        nullable=True,
    )
    notes = Column(Text, nullable=True)

    tenant = relationship("Tenant", backref="schools")
    scrape_urls = relationship(
        "SchoolScrapeUrl",
        back_populates="school",
        cascade="all, delete-orphan",
        foreign_keys="SchoolScrapeUrl.school_id",
    )
    primary_scrape_url = relationship(
        "SchoolScrapeUrl",
        foreign_keys=[scrape_url_id],
        post_update=True,
        uselist=False,
    )
    scraped_media = relationship(
        "ScrapedMedia",
        back_populates="school",
        cascade="all, delete-orphan",
        foreign_keys="ScrapedMedia.school_id",
    )


class SchoolScrapeUrl(BaseModel):
    """A confirmed archive-page URL configured for scraping per school."""

    __tablename__ = "school_scrape_urls"
    __table_args__ = (
        UniqueConstraint("school_id", "url", name="uq_scrape_url_school_url"),
    )

    school_id = Column(
        BigInteger,
        ForeignKey("schools.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    url = Column(Text, nullable=False)
    crawl_depth = Column(SmallInteger, default=1, nullable=False)
    use_playwright = Column(Boolean, default=False, nullable=False)
    confirmed_by_user_id = Column(
        BigInteger,
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    confirmed_at = Column(DateTime(timezone=True), nullable=True)
    last_http_status = Column(Integer, nullable=True)
    last_crawl_page_count = Column(Integer, nullable=True)
    is_active = Column(Boolean, default=True, nullable=False)

    school = relationship(
        "School",
        back_populates="scrape_urls",
        foreign_keys=[school_id],
    )


class ScrapedMedia(BaseModel):
    """
    One row per scraped media item (PDF / docx / audio / video / youtube).

    Links into the existing Document + vector store pipeline via
    `document_id`. Dedup is enforced via (school_id, content_hash) and
    indexed via `url_hash` for fast link-level skip.
    """

    __tablename__ = "scraped_media"
    __table_args__ = (
        UniqueConstraint(
            "school_id", "content_hash", name="uq_scraped_media_school_content"
        ),
        Index("ix_scraped_media_tenant_org", "tenant_id", "school_org_code"),
    )

    tenant_id = Column(
        BigInteger,
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    school_id = Column(
        BigInteger,
        ForeignKey("schools.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    school_org_code = Column(String(16), nullable=False, index=True)
    school_name = Column(String(512), nullable=True)
    district_type = Column(String(64), nullable=True)

    source_page_url = Column(Text, nullable=False)
    source_media_url = Column(Text, nullable=False)
    url_hash = Column(String(64), nullable=False, index=True)
    content_hash = Column(String(64), nullable=True, index=True)

    media_type = Column(String(16), nullable=False)
    file_extension = Column(String(16), nullable=True)
    original_name = Column(Text, nullable=True)
    document_type = Column(String(32), nullable=True)
    meeting_date = Column(Date, nullable=True)
    # Inferred 4-digit year from URL/filename/page context. Populated even
    # for status="skipped_year" rows so coverage can be audited without
    # re-parsing URLs.
    doc_year = Column(SmallInteger, nullable=True, index=True)

    s3_key_raw = Column(Text, nullable=True)
    s3_key_text = Column(Text, nullable=True)
    size_bytes = Column(BigInteger, nullable=True)
    duration_seconds = Column(Integer, nullable=True)

    document_id = Column(
        BigInteger,
        ForeignKey("documents.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    # Widened from 16 to 32: statuses such as "skipped_duplicate" (17) and
    # "skipped_too_large" (17) overflow a String(16) column.
    status = Column(String(32), default="discovered", nullable=False, index=True)
    error_message = Column(Text, nullable=True)
    scraped_at = Column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    ingested_at = Column(DateTime(timezone=True), nullable=True)

    school = relationship(
        "School",
        back_populates="scraped_media",
        foreign_keys=[school_id],
    )
    document = relationship("Document", foreign_keys=[document_id])
