"""
Pydantic schemas that double as the LLM prompt for the schema-driven page
classifier.

The field descriptions are read by the model via the JSON-schema passed in
`response_format`, so descriptive Field(...) comments ARE the prompt — there
is no separate prompt template to keep in sync. This is the central idea from
the schema-driven crawling blog post.

Promoted from scripts/school_data/schema_crawl_poc/schemas.py (experiment
branch) into the app layer so the hybrid crawler can reuse it. The POC scripts
now import from here so the two cannot drift.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

DATA_TYPES: tuple[str, ...] = (
    "board_minutes",
    "board_agenda",
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
            "meeting minutes or meeting agendas for a K-12 school board. "
            "Assign 0.5 if genuinely unsure, >0.5 up to 1.0 for increasing certainty. "
            "Assign 0.0 for links clearly unrelated to minutes or agendas "
            "(policies, book challenges, elections, news, staff directories, etc.)."
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
            + ". A page of MEETING MINUTES (formal records of past meetings) "
            "is 'board_minutes'; a page of AGENDAS (future/upcoming meeting "
            "topics) is 'board_agenda'. If a single page hosts BOTH agendas "
            "and minutes, choose 'board_agenda'. Use 'unknown' only if the "
            "page clearly hosts board meeting material but the specific type "
            "cannot be determined."
        ),
    )
    is_archive: bool = Field(
        description=(
            "True if this page is an ARCHIVE: it organizes documents for "
            "TWO OR MORE distinct PAST school years (e.g. sections/headers/"
            "folders for 2022-2023, 2023-2024). False if it hosts only the "
            "CURRENT school year's fresh documents, or a single year. A "
            "single 'old' page (e.g. a previous policy-manual revision) is "
            "NOT an archive. Use the current date provided in the prompt to "
            "decide which years count as 'past'."
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
            "True if THIS page's PRIMARY PURPOSE is to present meeting "
            "minutes or meeting agendas for a K-12 school board. This "
            "includes: (a) pages that embed meeting documents inline "
            "(PDF/DOCX/MP3/MP4/YouTube of board meetings), (b) pages that "
            "list direct download links for minutes or agendas, AND (c) "
            "archive/index pages whose main content is a list of meeting "
            "entries (by date/year) each linking to minutes or agenda "
            "documents. False for pages about policies, book challenges, "
            "elections, news, staff directories, calendars, or any content "
            "that is NOT meeting minutes or agendas."
        ),
    )
    has_data_links: bool = Field(
        description=(
            "True if THIS page's MAIN CONTENT BODY (not the site-wide "
            "header/footer navigation) contains links to subpages hosting "
            "meeting minutes or agendas, AND the page itself does not "
            "directly host them (has_data=false). Ignore links that appear "
            "in global nav/header/footer. Pages about policies, athletics, "
            "lunch menus, staff directories, or generic news are "
            "has_data_links=false unless they link to minutes/agendas in "
            "their main content."
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
            "Links on the current page that seem likely to lead to meeting "
            "minutes or meeting agendas. Include only same-domain links that "
            "plausibly lead to minutes or agenda pages. Exclude links to "
            "policies, elections, book challenges, news, staff directories, "
            "mailto:/tel:/# anchors, and off-domain links."
        ),
    )
