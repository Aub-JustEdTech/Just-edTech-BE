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
    last_scraped_at: datetime | None
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
    notes: str | None
    created_at: datetime
    updated_at: datetime
    scrape_urls: list[SchoolScrapeUrlOut] = Field(default_factory=list)
    scraped_media_count: int = 0


class SchoolListOut(BaseModel):
    """Paginated list of schools."""

    items: list[SchoolOut]
    total: int
    skip: int
    limit: int


# ---------------------------------------------------------------------------
# Scrape URLs
# ---------------------------------------------------------------------------


def _normalize_url(v: str) -> str:
    return v.strip().rstrip("/")


class ScrapeUrlCreate(BaseModel):
    url: str
    crawl_depth: int = Field(1, ge=0, le=3)
    use_playwright: bool = False

    @field_validator("url")
    @classmethod
    def strip_trailing_slash(cls, v: str) -> str:
        return _normalize_url(v)


class ScrapeUrlUpdate(BaseModel):
    url: str | None = Field(
        None,
        description="New URL text. Resets last_http_status/last_crawl_page_count"
        " since the edited page is unverified.",
    )
    crawl_depth: int | None = Field(None, ge=0, le=3)
    use_playwright: bool | None = None
    is_active: bool | None = None

    @field_validator("url")
    @classmethod
    def strip_trailing_slash(cls, v: str | None) -> str | None:
        return _normalize_url(v) if v is not None else v


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
    s3_url: str | None = None
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
# Scraped media filters (GET /schools/scraped-media/filters)
#
# The FE School Browser treats this endpoint as the single source of truth
# for how raw ScrapedMedia.status values group into filter chips, what the
# sortable fields are, and what date-preset values mean — see
# apps/tenants/.../utils/schoolBrowser/{dateUtils,mediaTypeUtils}.ts.
# ---------------------------------------------------------------------------

ScrapedMediaStatusGroup = Literal[
    "not_discovered", "discovered", "in_progress", "confirmed", "error"
]

# Raw ScrapedMedia.status values (see app/tasks/school_scraper_tasks.py and
# scripts/school_data/run_scrape_districts.py for every status="..." site)
# grouped into the five buckets the FE shows as filter chips. "not_discovered"
# has no raw value of its own — a school with zero ScrapedMedia rows reads as
# not-discovered by absence, not by a stored status.
STATUS_GROUP_RAW_VALUES: dict[ScrapedMediaStatusGroup, list[str]] = {
    "not_discovered": [],
    "discovered": ["discovered"],
    "in_progress": ["downloading", "ingesting"],
    "confirmed": ["completed"],
    "error": ["failed", "no_transcript", "skipped_duplicate", "skipped_year"],
}

STATUS_GROUP_LABELS: dict[ScrapedMediaStatusGroup, str] = {
    "not_discovered": "Not Discovered",
    "discovered": "Discovered",
    "in_progress": "In Progress",
    "confirmed": "Confirmed",
    "error": "Error",
}

# (value, label) for every sortable field list_scraped_media accepts.
SORT_FIELDS: list[tuple[str, str]] = [
    ("scraped_at", "Date Scraped"),
    ("original_name", "Name"),
    ("size_bytes", "Size"),
    ("status", "Status"),
]

# (value, label) for every date_from/date_to shortcut the FE offers.
# resolveDatePresetRange() on the FE is the single place these are turned
# into an actual { date_from, date_to } range — this list only has to name
# the presets, not define their math.
DATE_PRESETS: list[tuple[str, str]] = [
    ("today", "Today"),
    ("this_month", "This Month"),
    ("last_month", "Last Month"),
]

SortField = Literal["scraped_at", "original_name", "size_bytes", "status"]
SortOrder = Literal["asc", "desc"]


class ScrapedMediaStatusOption(BaseModel):
    value: ScrapedMediaStatusGroup
    label: str
    raw_values: list[str]


class SortFieldOption(BaseModel):
    value: str
    label: str


class DatePresetOption(BaseModel):
    value: str
    label: str


class ScrapedMediaFiltersOut(BaseModel):
    statuses: list[ScrapedMediaStatusOption]
    sort_fields: list[SortFieldOption]
    date_presets: list[DatePresetOption]


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
    has_scrape_urls: bool = False
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
