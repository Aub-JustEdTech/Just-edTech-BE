"""
Canonical taxonomy for the heatmap ingestion classifier.

Single source of truth for label sets and the response schema. Imported by:
  - scripts/classifier_eval/runner.py (Phase 1 eval harness)
  - app/services/heatmap_ingest/doc_classifier.py (doc-level)
  - app/services/heatmap_ingest/batch_classifier.py (chunk-level batch)

Frozen per the design discussion — adding a label requires updating this
file plus re-running classification on the affected chunks.
"""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator

# ── Label sets ────────────────────────────────────────────────────────────────

ENTITY_TYPES: tuple[str, ...] = (
    "board_minutes",
    "board_agenda",
    "policy_document",
    "book_challenge",
    "public_comment",
    "candidate_profile",
    "election_record",
    "news_media",
    "advocacy_intervention",
)
"""Doc-level single label. One value per document."""

TOPICS: tuple[str, ...] = (
    "sex_education",
    "curriculum_censorship",
    "parental_rights",
    "lgbtq_student_rights",
    "transgender_policy",
    "gender_identity",
    "school_board_election",
    "advocacy_organizing",
)
"""Chunk-level multi-label."""

ACTION_TYPES: tuple[str, ...] = (
    "instruction_reduced",
    "instruction_eliminated",
    "protection_adopted",
    "policy_proposed",
    "policy_debated",
    "book_challenged",
)
"""Chunk-level multi-label."""

SEX_ED_SUBTOPICS: tuple[str, ...] = (
    "comprehensive",
    "abstinence_only",
    "curriculum_change",
)
"""Chunk-level multi-label, only meaningful when 'sex_education' is in topics."""


# ── Response schema ───────────────────────────────────────────────────────────

class ChunkClassification(BaseModel):
    """Structured output returned by the chunk classifier (gpt-4o-mini)."""

    topics: list[str] = Field(
        default_factory=list,
        description="Subset of TOPICS substantively discussed in the chunk.",
    )
    action_types: list[str] = Field(
        default_factory=list,
        description="Subset of ACTION_TYPES evidenced in the chunk.",
    )
    subtopics: list[str] = Field(
        default_factory=list,
        description="Subset of SEX_ED_SUBTOPICS; only meaningful when sex_education is in topics.",
    )
    evidence_quote: str = Field(
        default="",
        description="Shortest verbatim span (max 30 words) justifying the strongest label; empty if no labels.",
    )
    off_topic: bool = Field(
        default=False,
        description="True if chunk is procedural/boilerplate (roll call, adjournment, page numbers) with no substantive content.",
    )

    @field_validator("topics")
    @classmethod
    def _validate_topics(cls, v: list[str]) -> list[str]:
        invalid = [t for t in v if t not in TOPICS]
        if invalid:
            raise ValueError(f"Unknown topics: {invalid}. Allowed: {TOPICS}")
        return v

    @field_validator("action_types")
    @classmethod
    def _validate_action_types(cls, v: list[str]) -> list[str]:
        invalid = [a for a in v if a not in ACTION_TYPES]
        if invalid:
            raise ValueError(f"Unknown action_types: {invalid}. Allowed: {ACTION_TYPES}")
        return v

    @field_validator("subtopics")
    @classmethod
    def _validate_subtopics(cls, v: list[str]) -> list[str]:
        invalid = [s for s in v if s not in SEX_ED_SUBTOPICS]
        if invalid:
            raise ValueError(f"Unknown subtopics: {invalid}. Allowed: {SEX_ED_SUBTOPICS}")
        return v


class DocClassification(BaseModel):
    """Doc-level classification (one per document, not per chunk)."""

    entity_type: str = Field(..., description="One of ENTITY_TYPES.")
    doc_kind: str = Field(
        ...,
        description="agenda | minutes | packet | resolution | policy | news | other",
    )
    meeting_date: str | None = Field(
        default=None,
        description="ISO date (YYYY-MM-DD) if extractable from filename/first page, else None.",
    )

    @field_validator("entity_type")
    @classmethod
    def _validate_entity_type(cls, v: str) -> str:
        if v not in ENTITY_TYPES:
            raise ValueError(f"Unknown entity_type: {v}. Allowed: {ENTITY_TYPES}")
        return v


# Convenience: all multi-label sets combined, for uniform eval math.
ALL_CHUNK_LABELS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("topics", TOPICS),
    ("action_types", ACTION_TYPES),
    ("subtopics", SEX_ED_SUBTOPICS),
)
