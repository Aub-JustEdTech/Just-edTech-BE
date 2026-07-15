"""
Pydantic schemas that double as the LLM prompt.

The field descriptions are read by the model via the JSON-schema passed in
`response_format`, so descriptive Field(...) comments ARE the prompt —
there is no separate prompt template to keep in sync. This is the central
idea from the schema-driven crawling blog post.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

# Data types mirror the heatmap taxonomy (app/services/heatmap_ingest/taxonomy.py)
# so a discovered page can later be routed to the right ingest pipeline.
DATA_TYPES: tuple[str, ...] = (
    "board_minutes",
    "board_agenda",
    "policy_document",
    "book_challenge",
    "public_comment",
    "candidate_profile",
    "election_record",
    "news_media",
    "advocacy_intervention",
    "unknown",
)


class PossibleRelevantPage(BaseModel):
    """A link on the current page that may lead to relevant data."""

    url: str = Field(
        description="Absolute URL of the link, exactly as it appears in the page markup (resolved relative to the page)."
    )
    confidence: float = Field(
        description=(
            "0.0–1.0 likelihood that following this link leads to a page hosting "
            "K-12 school board documents (agendas, minutes, policies, public "
            "comments, book challenges, election materials, news, advocacy). "
            "Assign 0.5 if genuinely unsure, >0.5 up to 1.0 for increasing certainty."
        ),
    )
    reason: str | None = Field(
        default=None,
        description="One short clause explaining the confidence (e.g. 'path contains /minutes/').",
    )


class DataPageInfo(BaseModel):
    """Metadata about a page that directly hosts relevant documents/media."""

    data_type: str = Field(
        description=(
            "Best single label for what this page hosts. One of: "
            + ", ".join(DATA_TYPES)
            + ". Use 'unknown' only if the page clearly hosts board material "
            "but the specific type cannot be determined."
        ),
    )
    is_archive: bool = Field(
        description=(
            "True if this page is an ARCHIVE of a past school year's documents "
            "(e.g. /board/docs/archive/2023-2024, /board/2022-2023-minutes). "
            "False if it hosts the CURRENT school year's fresh documents. "
            "Use the current date provided in the prompt to decide."
        ),
    )
    data_years_available: list[int] = Field(
        default_factory=list,
        description=(
            "Calendar years for which documents are available on this page, "
            "e.g. [2024, 2025] for the 2024-2025 school year. Empty list if "
            "the page does not organize by year or if years cannot be inferred."
        ),
    )
    confidence: float = Field(
        description="0.0–1.0 confidence in the data_type and is_archive labels.",
    )


class RelevantPage(BaseModel):
    """LLM classification of one crawled page."""

    url: str = Field(description="The URL of the page being classified.")
    title: str = Field(
        description="Page title (from <title>, og:title, or first <h1>)."
    )
    has_data: bool = Field(
        description=(
            "True if THIS page directly hosts the desired K-12 school board "
            "documents or media (PDFs of agendas/minutes/policies, video/audio "
            "of meetings). False if the page is a landing/section/index page."
        ),
    )
    has_data_links: bool = Field(
        description=(
            "True if THIS page contains links to subpages that host the "
            "desired documents, even if the page itself does not host them."
        ),
    )
    description: str | None = Field(
        default=None,
        description="A brief one-sentence description of the page's content.",
    )
    data_page_info: DataPageInfo | None = Field(
        default=None,
        description=(
            "Set this field ONLY when has_data is True. Omit/leave null when "
            "the page does not directly host documents."
        ),
    )
    possible_relevant_pages: list[PossibleRelevantPage] = Field(
        default_factory=list,
        description=(
            "Links on the current page that seem likely to lead to relevant "
            "data. Include only same-domain links that plausibly lead to "
            "board/policy/meeting material. Exclude mailto:/tel:/# anchors "
            "and off-domain links."
        ),
    )
