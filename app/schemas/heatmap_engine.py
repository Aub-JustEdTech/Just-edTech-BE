"""
Pydantic schemas for the Heatmap Generation Engine.

The engine counts Qdrant chunk instances per district, filtered by V1
`topic_tags` categories and academic-year-based timeframe presets.
Reads directly from the vector store (not the `heatmap_aggregate` table).
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel


class TimeframePreset(str, Enum):
    """Rolling timeframe presets that snap to academic-year buckets."""

    MONTH = "month"
    LAST_2_MONTHS = "last_2_months"
    QUARTER = "quarter"
    YEAR = "year"
    TWO_YEARS = "2_years"
    THREE_YEARS = "3_years"


class TopicCategory(str, Enum):
    """V1 `topic_tags` categories (see vocabulary_packs/core.py)."""

    SEXED = "sexed"
    LGBTQ = "lgbtq"
    CENSORSHIP = "censorship"
    GOVERNANCE = "governance"
    ADVOCACY = "advocacy"


class DistrictCountItem(BaseModel):
    """One row in the all-districts count response."""

    org_code: str
    district_name: str
    district_type: Literal["public", "charter"] = "public"
    state: str
    # Chunks matching ALL selected categories combined. Because `topic_tags`
    # is an array, one chunk can match several categories, so this is <= the
    # sum of the per-category counts (never equal to their sum).
    chunk_count: int
    # Only populated when the request passes `breakdown=true`. `top_category`
    # is the highest-counting of the selected categories for this district;
    # it stays None for districts with no matching chunks.
    top_category: TopicCategory | None = None
    top_category_count: int = 0
    # Full per-category mention counts, populated alongside `top_category`
    # when `breakdown=true`. Only categories with at least one match are
    # present. Powers the report's topic-mentions-per-district breakdown.
    category_counts: dict[TopicCategory, int] = {}


class DistrictCountResponse(BaseModel):
    """Aggregate response for `GET /heatmap/engine/districts`."""

    timeframe: TimeframePreset
    categories: list[TopicCategory]
    total_districts: int
    total_chunks: int
    districts: list[DistrictCountItem]
    # Echoed back only when the request used the custom date-range filter
    # (mutually exclusive with `timeframe` taking effect); ISO date strings.
    start_date: str | None = None
    end_date: str | None = None


class EngineCitationItem(BaseModel):
    """One chunk citation in the per-district drill-down."""

    document_id: str | None = None
    document_title: str
    date: str | None
    snippet: str
    # Original scraped media file (PDF / Drive / GetFile link).
    source_url: str = ""
    # Listing / agenda-minutes page the media was discovered on.
    source_page_url: str = ""
    s3_url: str | None = None
    page_number: int | None = None
    topic_tags: list[dict[str, Any]] = []


class DistrictCitationsEngineResponse(BaseModel):
    """Per-district citation payload for the engine endpoints."""

    org_code: str
    district_name: str
    timeframe: TimeframePreset
    categories: list[TopicCategory]
    citations: list[EngineCitationItem]
