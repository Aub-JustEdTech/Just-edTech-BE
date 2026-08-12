"""
Pydantic schemas for the generalised school website scraper endpoints.
"""

from typing import Annotated, Literal

from pydantic import BaseModel, Field, field_validator


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

    @field_validator("crawl_depth")
    @classmethod
    def clamp_depth(cls, v: int) -> int:
        return max(0, min(v, 3))


class MediaFileResult(BaseModel):
    """A single discovered media file."""

    name: str | None
    url: str
    file_extension: str
    media_type: Literal["video", "audio", "document"]
    size_bytes: int | None
    source_page_url: str


class MediaTypeSummary(BaseModel):
    """Count of each media type found on the scraped URL."""

    video: int = 0
    audio: int = 0
    document: int = 0


class ScrapeMediaResponse(BaseModel):
    """Response from the media-scraping step."""

    source_url: str
    pages_crawled: int
    total_media_found: int
    media_type_summary: MediaTypeSummary
    media_files: list[MediaFileResult]


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


class IngestScrapedMediaRequest(BaseModel):
    """Request body for the manual ingestion-trigger endpoint.

    Mirrors scripts/school_data/bulk_ingest_scraped_media.py: dispatches the
    existing `ingest_scraped_media` Celery task for scraped_media rows
    matching the filter, scoped to the caller's tenant. `status` is
    restricted to 'discovered' (the normal queue) and 'failed' (manual
    retry) -- 'skipped_year' rows have their own re-evaluation flow via
    /backfill-years and must not be re-dispatched directly here.
    """

    school_id: int | None = Field(
        None,
        description="Scope to a single school. Omit to dispatch across the "
        "whole tenant.",
    )
    status: Literal["discovered", "failed"] = Field(
        "discovered",
        description="scraped_media status to pull rows from.",
    )
    limit: int = Field(
        200,
        ge=1,
        le=1000,
        description="Max rows to dispatch in this call. Call again to "
        "continue past this page.",
    )
    reset_stale_minutes: int = Field(
        0,
        ge=0,
        description="Reset rows stuck in 'downloading'/'ingesting' longer "
        "than this many minutes back to 'discovered' before dispatching. "
        "0 disables.",
    )


class IngestScrapedMediaResponse(BaseModel):
    """Response from the manual ingestion-trigger endpoint."""

    enqueued: int
    reset_stale: int
    status_counts_before: dict[str, int]
    message: str
