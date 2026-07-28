"""
Pydantic schemas for the school scraping knowledge base endpoints.

Covers: schools CRUD, scrape URL configuration, and scraped media records.
"""

from datetime import date, datetime
from typing import Literal

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
    has_confirmed_scrape_url: bool = False
    confirmed_scrape_url: str | None = None


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
    source_page_url: str
    source_media_url: str
    url_hash: str
    content_hash: str | None
    media_type: str
    file_extension: str | None
    original_name: str | None
    document_type: str | None
    meeting_date: date | None
    doc_year: int | None = None
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


MediaTypeLiteral = Literal["video", "audio", "document", "youtube"]

ConfirmationStatusFilter = Literal["added", "not_added"]


# ---------------------------------------------------------------------------
# Scrape URL confirmation (JSON-backed candidates)
# ---------------------------------------------------------------------------


CandidateSource = Literal["discovered", "manual"]


class ScrapeUrlCandidateOut(BaseModel):
    """One ranked candidate URL (discovered JSON and/or manually added)."""

    rank: int
    url: str
    score: int
    matched_keywords: list[str] = Field(default_factory=list)
    data_type: str | None = None
    is_archive: bool = False
    data_years_available: list[int] = Field(default_factory=list)
    source: CandidateSource = "discovered"
    is_selected: bool = False
    scrape_url_id: int | None = None


class SchoolCandidateReviewOut(BaseModel):
    """School row merged with JSON discovery candidates and DB confirm state."""

    school_id: int | None = None
    org_code: str
    name: str
    website: str | None = None
    in_database: bool = False
    has_confirmed_scrape_url: bool = False
    confirmed_scrape_url: str | None = None
    confirmed_scrape_url_id: int | None = None
    discovery_method: str | None = None
    total_urls_scanned: int = 0
    total_candidates: int = 0
    candidates: list[ScrapeUrlCandidateOut] = Field(default_factory=list)


class SchoolCandidateReviewListOut(BaseModel):
    items: list[SchoolCandidateReviewOut]
    total: int
    skip: int
    limit: int
    added_count: int
    not_added_count: int
