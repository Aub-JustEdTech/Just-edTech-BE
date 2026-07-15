"""
School, scrape URL, scrape run, scrape job, and scraped media models.

These back the district-level school knowledge base: per-tenant MA school
districts (seeded from scripts/school_data/output/school_names.json), their
confirmed archive-page URLs to scrape every 14 days, run/job tracking for
each cycle, and per-media-item records that link into the existing Document
+ vector store pipeline with content-hash dedup.
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
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship

from app.models.base import BaseModel


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class School(BaseModel):
    """
    A district-level school row.

    Seeded from scripts/school_data/output/school_names.json (396 MA
    districts). Scoped per-tenant via (tenant_id, org_code) uniqueness.
    `last_scrapped_at` is denormalized for fast FE display and is updated
    at the end of a successful scrape job for the school.
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
    scrape_jobs = relationship(
        "SchoolScrapeJob",
        back_populates="school",
        cascade="all, delete-orphan",
        foreign_keys="SchoolScrapeJob.school_id",
    )
    url_discovery = relationship(
        "SchoolUrlDiscovery",
        back_populates="school",
        cascade="all, delete-orphan",
        uselist=False,
    )
    url_candidates = relationship(
        "SchoolUrlCandidate",
        back_populates="school",
        cascade="all, delete-orphan",
        foreign_keys="SchoolUrlCandidate.school_id",
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
    jobs = relationship(
        "SchoolScrapeJob",
        back_populates="scrape_url",
        cascade="all, delete-orphan",
        foreign_keys="SchoolScrapeJob.scrape_url_id",
    )


class ScrapeRun(BaseModel):
    """One row per 14-day-cycle batch execution across all active schools."""

    __tablename__ = "scrape_runs"

    tenant_id = Column(
        BigInteger,
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    triggered_by = Column(String(32), nullable=False)
    status = Column(String(16), default="pending", nullable=False, index=True)
    started_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    total_schools = Column(Integer, default=0, nullable=False)
    schools_completed = Column(Integer, default=0, nullable=False)
    schools_failed = Column(Integer, default=0, nullable=False)
    schools_skipped = Column(Integer, default=0, nullable=False)
    media_found = Column(Integer, default=0, nullable=False)
    media_new = Column(Integer, default=0, nullable=False)
    media_skipped_duplicate = Column(Integer, default=0, nullable=False)
    error_summary = Column(JSONB, nullable=True)

    tenant = relationship("Tenant", backref="scrape_runs")
    jobs = relationship(
        "SchoolScrapeJob",
        back_populates="run",
        cascade="all, delete-orphan",
        foreign_keys="SchoolScrapeJob.run_id",
    )


class SchoolScrapeJob(BaseModel):
    """One job per (school x scrape URL) within a ScrapeRun."""

    __tablename__ = "school_scrape_jobs"

    run_id = Column(
        BigInteger,
        ForeignKey("scrape_runs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    school_id = Column(
        BigInteger,
        ForeignKey("schools.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    scrape_url_id = Column(
        BigInteger,
        ForeignKey("school_scrape_urls.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    status = Column(String(16), default="pending", nullable=False, index=True)
    started_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    pages_crawled = Column(Integer, default=0, nullable=False)
    media_found = Column(Integer, default=0, nullable=False)
    media_new = Column(Integer, default=0, nullable=False)
    media_skipped_duplicate = Column(Integer, default=0, nullable=False)
    error_message = Column(Text, nullable=True)
    scrape_result = Column(JSONB, nullable=True)

    run = relationship("ScrapeRun", back_populates="jobs", foreign_keys=[run_id])
    school = relationship(
        "School", back_populates="scrape_jobs", foreign_keys=[school_id]
    )
    scrape_url = relationship(
        "SchoolScrapeUrl", back_populates="jobs", foreign_keys=[scrape_url_id]
    )
    scraped_media = relationship(
        "ScrapedMedia",
        back_populates="scrape_job",
        foreign_keys="ScrapedMedia.scrape_job_id",
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
    scrape_job_id = Column(
        BigInteger,
        ForeignKey("school_scrape_jobs.id", ondelete="SET NULL"),
        nullable=True,
    )
    scrape_run_id = Column(
        BigInteger,
        ForeignKey("scrape_runs.id", ondelete="SET NULL"),
        nullable=True,
    )

    source_page_url = Column(Text, nullable=False)
    source_media_url = Column(Text, nullable=False)
    url_hash = Column(String(64), nullable=False, index=True)
    content_hash = Column(String(64), nullable=True, index=True)

    media_type = Column(String(16), nullable=False)
    file_extension = Column(String(16), nullable=True)
    original_name = Column(Text, nullable=True)
    document_type = Column(String(32), nullable=True)
    meeting_date = Column(Date, nullable=True)

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

    status = Column(String(16), default="discovered", nullable=False, index=True)
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
    scrape_job = relationship(
        "SchoolScrapeJob",
        back_populates="scraped_media",
        foreign_keys=[scrape_job_id],
    )
    document = relationship("Document", foreign_keys=[document_id])
