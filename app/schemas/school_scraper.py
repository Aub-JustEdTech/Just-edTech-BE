"""
Pydantic schemas for the generalised school website scraper endpoints.
"""

from typing import Annotated

from pydantic import BaseModel, Field, field_validator

from app.schemas.schools import MediaTypeLiteral


class DiscoverRequest(BaseModel):
    """Request body for the URL-discovery step."""

    base_url: str
    max_candidates: int = 10
    use_playwright: Annotated[
        bool,
        Field(
            default=False,
            description=(
                "Force Playwright (headless Chromium) for the follow-up crawl. "
                "When False (default), the scraper auto-detects JS-rendered sites "
                "by inspecting the raw HTML for framework fingerprints (Finalsite, "
                "Next.js, Angular, etc.) and launches Playwright automatically if "
                "needed. Set to True only to override detection and always use the "
                "browser."
            ),
        ),
    ] = False

    @field_validator("base_url")
    @classmethod
    def strip_trailing_slash(cls, v: str) -> str:
        return v.strip().rstrip("/")


class CandidateUrl(BaseModel):
    """A single candidate URL that matched meeting-related keywords.

    The schema-crawler fields (data_type, is_archive, data_years_available)
    are populated only when SCHOOL_SCRAPER_RANKING_MODE is `llm` or `both`;
    the keyword path leaves them at their defaults (None / False / []).
    """

    url: str
    matched_keywords: list[str]
    score: int
    data_type: str | None = None
    is_archive: bool = False
    data_years_available: list[int] = []


class DiscoverResponse(BaseModel):
    """Response from the URL-discovery step."""

    base_url: str
    discovery_method: str
    total_urls_scanned: int
    total_candidates: int
    candidates: list[CandidateUrl]
    # Which ranking mode produced these candidates. Mirrors the
    # SCHOOL_SCRAPER_RANKING_MODE setting: "keyword" (default), "llm", or "both".
    ranking_mode: str = "keyword"


class ScrapeMediaRequest(BaseModel):
    """Request body for the media-scraping step."""

    url: str
    crawl_depth: int = 1
    school_id: int | None = Field(
        None,
        description="School to attach discovered media to. Required when "
        "persist=True. When supplied together with scrape_url_id, the scrape "
        "result (last_scraped_at/last_http_status/last_crawl_page_count) is "
        "also persisted onto that SchoolScrapeUrl row. Omit for a stateless "
        "preview scrape (e.g. during discovery review).",
    )
    scrape_url_id: int | None = Field(
        None, description="See school_id."
    )
    persist: bool = Field(
        False,
        description="When True, save discovered media to scraped_media and "
        "enqueue ingestion for newly created rows only. Defaults to False so "
        "the existing preview-only contract is unchanged.",
    )

    @field_validator("crawl_depth")
    @classmethod
    def clamp_depth(cls, v: int) -> int:
        return max(0, min(v, 3))


class MediaFileResult(BaseModel):
    """A single discovered media file."""

    name: str | None
    url: str
    # Nullable: a YouTube video has no file extension.
    file_extension: str | None = None
    media_type: MediaTypeLiteral
    size_bytes: int | None
    source_page_url: str


class MediaTypeSummary(BaseModel):
    """Count of each media type found on the scraped URL."""

    video: int = 0
    audio: int = 0
    document: int = 0
    youtube: int = 0


class ScrapeMediaResponse(BaseModel):
    """Response from the media-scraping step."""

    source_url: str
    scrape_url_id: int | None = None
    success: bool = True
    http_status: int | None = None
    pages_crawled: int
    total_media_found: int
    media_type_summary: MediaTypeSummary
    media_files: list[MediaFileResult]
    # Populated only when persist=True; zero on a preview call.
    persisted: int = 0
    skipped_duplicates: int = 0
    enqueued: int = 0
    scraped_media_ids: list[int] = Field(default_factory=list)


class ScrapeMediaBatchRequest(BaseModel):
    """Request body for scraping multiple confirmed URLs for one school.

    Unlike /scrape-media, this endpoint has no preview-only mode: it backs
    the "Scrape selected" action, which always saves discovered media to
    scraped_media and enqueues ingestion for newly created rows.
    """

    school_id: int
    scrape_url_ids: list[int] = Field(..., min_length=1)
    crawl_depth: int = 1

    @field_validator("crawl_depth")
    @classmethod
    def clamp_depth(cls, v: int) -> int:
        return max(0, min(v, 3))


class ScrapeMediaBatchResponse(BaseModel):
    """Response from the batch media-scraping endpoint — one result per URL."""

    results: list[ScrapeMediaResponse]


class ScrapeMediaBatchTaskResponse(BaseModel):
    """Returned immediately by POST /scrape-media-batch — the scrape itself
    runs in the background. Poll GET /scrape-media-batch/status with this
    task_id for the actual results."""

    task_id: str
    status: str = "PENDING"


class ScrapeMediaBatchStatusResponse(BaseModel):
    """Poll this until `running` is False, then read `results`."""

    running: bool
    task_status: str | None = None
    results: list[ScrapeMediaResponse] | None = None
    # Set only on a validation failure inside the task (bad school_id or
    # scrape_url_ids) — mirrors what the old synchronous endpoint used to
    # raise as an immediate 404.
    error: str | None = None


class BackfillYearsRequest(BaseModel):
    """Request body for the year-filter backfill endpoint.

    Re-evaluates scraped_media rows previously marked status='skipped_year'
    against the current SCHOOL_SCRAPER_ALLOWED_YEARS set and re-queues any
    that now fall in range. Optional school_id scopes to a single school.
    """

    school_id: int | None = Field(
        None,
        description="Scope to a single school. Omit to backfill all schools "
        "in the caller's tenant.",
    )


class BackfillYearsResponse(BaseModel):
    """Response from the year-filter backfill endpoint."""

    enqueued: int
    skipped: int
    message: str


class ScrapeAllResponse(BaseModel):
    """Response from triggering a tenant-wide sweep of every active source."""

    task_id: str
    status: str
    message: str


class ScrapeStatusResponse(BaseModel):
    """Whether a tenant-wide sweep is currently running, for the FE to know
    when to keep the common and per-school scrape buttons disabled."""

    running: bool
    task_id: str | None = None
    task_status: str | None = None
