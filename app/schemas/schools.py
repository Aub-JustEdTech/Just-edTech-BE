"""
Pydantic schemas for the school scraping knowledge base endpoints.

Covers: schools CRUD, scrape URL configuration, scrape run/job tracking,
and scraped media records.
"""

from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


# ---------------------------------------------------------------------------
# Schools
# ---------------------------------------------------------------------------


class SchoolBase(BaseModel):
    org_code: str = Field(..., max_length=16)
    name: str = Field(..., max_length=512)
    district_type: str = Field(..., max_length=64)
    website: str | None = None
    is_active: bool = True
    notes: str | None = None


class SchoolCreate(SchoolBase):
    tenant_id: int | None = Field(
        None,
        description="Defaults to the current user's tenant if omitted.",
    )


class SchoolUpdate(BaseModel):
    org_code: str | None = Field(None, max_length=16)
    name: str | None = Field(None, max_length=512)
    district_type: str | None = Field(None, max_length=64)
    website: str | None = None
    is_active: bool | None = None
    notes: str | None = None


class SchoolScrapeUrlOut(BaseModel):
    """Outbound schema for a confirmed scrape URL."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    school_id: int
    url: str
    crawl_depth: int
    use_playwright: bool
    confirmed_by_user_id: int | None
    confirmed_at: datetime | None
    last_http_status: int | None
    last_crawl_page_count: int | None
    is_active: bool
    created_at: datetime
    updated_at: datetime


class SchoolOut(BaseModel):
    """Outbound schema for a school, with denormalized scrape stats."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    tenant_id: int
    org_code: str
    name: str
    district_type: str
    website: str | None
    last_scrapped_at: datetime | None
    is_active: bool
    scrape_url_id: int | None
    notes: str | None
    created_at: datetime
    updated_at: datetime
    scrape_urls: list[SchoolScrapeUrlOut] = Field(default_factory=list)
    scraped_media_count: int = 0
    last_run_status: str | None = None


class SchoolListOut(BaseModel):
    """Paginated list of schools."""

    items: list[SchoolOut]
    total: int
    skip: int
    limit: int


# ---------------------------------------------------------------------------
# Scrape URLs
# ---------------------------------------------------------------------------


class ScrapeUrlCreate(BaseModel):
    url: str
    crawl_depth: int = Field(1, ge=0, le=3)
    use_playwright: bool = False
    is_primary: bool = Field(
        False,
        description="If True, set this as the school's scrape_url_id.",
    )

    @field_validator("url")
    @classmethod
    def strip_trailing_slash(cls, v: str) -> str:
        return v.strip().rstrip("/")


class ScrapeUrlUpdate(BaseModel):
    crawl_depth: int | None = Field(None, ge=0, le=3)
    use_playwright: bool | None = None
    is_active: bool | None = None
    is_primary: bool | None = None


# ---------------------------------------------------------------------------
# Scrape runs & jobs
# ---------------------------------------------------------------------------


class ScrapeRunOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    tenant_id: int
    triggered_by: str
    status: str
    started_at: datetime | None
    completed_at: datetime | None
    total_schools: int
    schools_completed: int
    schools_failed: int
    schools_skipped: int
    media_found: int
    media_new: int
    media_skipped_duplicate: int
    error_summary: dict | None
    created_at: datetime
    updated_at: datetime


class ScrapeRunListOut(BaseModel):
    items: list[ScrapeRunOut]
    total: int
    skip: int
    limit: int


class SchoolScrapeJobOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    run_id: int
    school_id: int
    scrape_url_id: int
    status: str
    started_at: datetime | None
    completed_at: datetime | None
    pages_crawled: int
    media_found: int
    media_new: int
    media_skipped_duplicate: int
    error_message: str | None
    scrape_result: dict | None
    created_at: datetime
    updated_at: datetime


class ScrapeRunDetailOut(ScrapeRunOut):
    jobs: list[SchoolScrapeJobOut] = Field(default_factory=list)


class TriggerRunRequest(BaseModel):
    """Manually trigger a full scrape cycle for a tenant."""

    only_active: bool = True


class TriggerSchoolScrapeRequest(BaseModel):
    """Manually trigger a scrape for a single school."""

    scrape_url_id: int | None = Field(
        None,
        description="Defaults to the school's primary scrape_url_id.",
    )


class ScrapeJobAck(BaseModel):
    """Acknowledgement returned when a scrape is enqueued."""

    run_id: int
    job_id: int | None
    status: str
    message: str


# ---------------------------------------------------------------------------
# Scraped media
# ---------------------------------------------------------------------------


class ScrapedMediaOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    tenant_id: int
    school_id: int
    school_org_code: str
    school_name: str | None
    district_type: str | None
    scrape_job_id: int | None
    scrape_run_id: int | None
    source_page_url: str
    source_media_url: str
    url_hash: str
    content_hash: str | None
    media_type: str
    file_extension: str | None
    original_name: str | None
    document_type: str | None
    meeting_date: date | None
    s3_key_raw: str | None
    s3_key_text: str | None
    size_bytes: int | None
    duration_seconds: int | None
    document_id: int | None
    status: str
    error_message: str | None
    scraped_at: datetime
    ingested_at: datetime | None
    created_at: datetime
    updated_at: datetime


class ScrapedMediaListOut(BaseModel):
    items: list[ScrapedMediaOut]
    total: int
    skip: int
    limit: int


# ---------------------------------------------------------------------------
# Discovery (stateless — for one-time FE confirmation only)
# ---------------------------------------------------------------------------


class DiscoverForSchoolRequest(BaseModel):
    """Run URL discovery against a school's website."""

    max_candidates: int = 10
    use_playwright: bool = False


# ---------------------------------------------------------------------------
# Webhook / chat consumer passthrough (unused placeholder kept for parity)
# ---------------------------------------------------------------------------

MediaTypeLiteral = Literal["video", "audio", "document", "youtube"]
