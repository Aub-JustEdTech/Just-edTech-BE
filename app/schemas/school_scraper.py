"""
Pydantic schemas for the generalised school website scraper endpoints.
"""

from typing import Literal

from pydantic import BaseModel, field_validator


class DiscoverRequest(BaseModel):
    """Request body for the URL-discovery step."""

    base_url: str
    max_candidates: int = 10
    use_playwright: bool = False

    @field_validator("base_url")
    @classmethod
    def strip_trailing_slash(cls, v: str) -> str:
        return v.strip().rstrip("/")


class CandidateUrl(BaseModel):
    """A single candidate URL that matched meeting-related keywords."""

    url: str
    matched_keywords: list[str]
    score: int


class DiscoverResponse(BaseModel):
    """Response from the URL-discovery step."""

    base_url: str
    discovery_method: str
    total_urls_scanned: int
    total_candidates: int
    candidates: list[CandidateUrl]


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
