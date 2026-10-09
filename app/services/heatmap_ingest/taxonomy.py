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

# ── Heatmap V1 doc-level enums (spec: meeting_doc_type / meeting_body) ──────

MEETING_DOC_TYPES: tuple[str, ...] = (
    "Minutes",
    "Agenda",
    "Agenda Attachment",
    "Public Comment Transcript",
    "Policy Document",
    "Presentation Slide",
)
"""Doc-level single label. Distinct from the file-extension `document_type`."""

MEETING_BODIES: tuple[str, ...] = (
    "Full Board",
    "Curriculum Subcommittee",
    "Policy Subcommittee",
    "Public Hearing",
    "Special Meeting",
)
"""Doc-level single label."""

# ── Heatmap V1 chunk-level enums (spec: action_stage / speaker_role) ────────

ACTION_STAGES: tuple[str, ...] = (
    "Discussion Only",
    "Public Comment",
    "Motion Made",
    "Vote — Passed",
    "Vote — Failed",
    "Vote — Tabled",
    "Policy First Reading",
    "Policy Adoption (Final)",
    "Presentation/Report Given",
    "Correspondence Referenced",
)
"""Chunk-level single label (one per chunk, nullable)."""

SPEAKER_ROLES: tuple[str, ...] = (
    "Board Member",
    "Superintendent/Admin",
    "Public Commenter",
    "Student",
    "External Presenter",
)
"""Chunk-level role for each speaker entry. Free-text name in V1."""


# ── Response schema ───────────────────────────────────────────────────────────

class TopicTag(BaseModel):
    """A single (category, subtopic) topic tag, per spec A5.

    Flat two-column storage so topic_tags become native facets in
    Postgres and Qdrant payload with no string parsing needed.
    """

    category: str = Field(..., description="One of the topic category prefixes.")
    subtopic: str = Field(..., description="Subtopic within the category.")


class Speaker(BaseModel):
    """A single speaker mention. Free-text name in V1 (no person resolution)."""

    name: str = Field(..., description="Free-text speaker name (unresolved in V1).")
    role: str = Field(..., description="One of SPEAKER_ROLES.")


class ChunkClassification(BaseModel):
    """Structured output returned by the chunk classifier (gpt-4o-mini).

    V1 additions (spec A2/A3/A5): `topic_tags`, `action_stage`, `speakers`.
    The legacy `topics` / `action_types` / `subtopics` arrays are preserved
    for backward compatibility with the heatmap_aggregate roll-up and the
    existing retrieval path; do NOT drop until the follow-up migration.
    """

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
    # V1 fields ───────────────────────────────────────────────────────────────
    topic_tags: list[TopicTag] = Field(
        default_factory=list,
        description=(
            "Flat (category, subtopic) pairs from the universal core + state "
            "pack. Multi-tag allowed per A9."
        ),
    )
    action_stage: str | None = Field(
        default=None,
        description="One of ACTION_STAGES, or null if no single stage applies.",
    )
    speakers: list[Speaker] = Field(
        default_factory=list,
        description="Speakers mentioned in the chunk (free-text name in V1).",
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

    @field_validator("action_stage")
    @classmethod
    def _validate_action_stage(cls, v: str | None) -> str | None:
        if v is None:
            return None
        if v not in ACTION_STAGES:
            raise ValueError(
                f"Unknown action_stage: {v}. Allowed: {ACTION_STAGES}"
            )
        return v

    @field_validator("speakers")
    @classmethod
    def _validate_speakers(cls, v: list[Speaker]) -> list[Speaker]:
        invalid = [s.role for s in v if s.role not in SPEAKER_ROLES]
        if invalid:
            raise ValueError(f"Unknown speaker roles: {invalid}. Allowed: {SPEAKER_ROLES}")
        return v


class DocClassification(BaseModel):
    """Doc-level classification (one per document, not per chunk).

    V1 additions (spec: meeting_doc_type / meeting_body). `entity_type`,
    `doc_kind`, `meeting_date` preserved for backward compatibility.
    """

    entity_type: str = Field(..., description="One of ENTITY_TYPES.")
    doc_kind: str = Field(
        ...,
        description="agenda | minutes | packet | resolution | policy | news | other",
    )
    meeting_date: str | None = Field(
        default=None,
        description="ISO date (YYYY-MM-DD) if extractable from filename/first page, else None.",
    )
    meeting_doc_type: str | None = Field(
        default=None,
        description="One of MEETING_DOC_TYPES (Minutes / Agenda / etc.), or null.",
    )
    meeting_body: str | None = Field(
        default=None,
        description="One of MEETING_BODIES (Full Board / subcommittee / etc.), or null.",
    )

    @field_validator("entity_type")
    @classmethod
    def _validate_entity_type(cls, v: str) -> str:
        if v not in ENTITY_TYPES:
            raise ValueError(f"Unknown entity_type: {v}. Allowed: {ENTITY_TYPES}")
        return v

    @field_validator("meeting_doc_type")
    @classmethod
    def _validate_meeting_doc_type(cls, v: str | None) -> str | None:
        if v is None:
            return None
        if v not in MEETING_DOC_TYPES:
            raise ValueError(
                f"Unknown meeting_doc_type: {v}. Allowed: {MEETING_DOC_TYPES}"
            )
        return v

    @field_validator("meeting_body")
    @classmethod
    def _validate_meeting_body(cls, v: str | None) -> str | None:
        if v is None:
            return None
        if v not in MEETING_BODIES:
            raise ValueError(
                f"Unknown meeting_body: {v}. Allowed: {MEETING_BODIES}"
            )
        return v


# Convenience: all multi-label sets combined, for uniform eval math.
ALL_CHUNK_LABELS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("topics", TOPICS),
    ("action_types", ACTION_TYPES),
    ("subtopics", SEX_ED_SUBTOPICS),
)

# Convenience: all single-label enum sets (doc + chunk level) for eval.
ALL_SINGLE_LABELS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("meeting_doc_type", MEETING_DOC_TYPES),
    ("meeting_body", MEETING_BODIES),
    ("action_stage", ACTION_STAGES),
    ("speaker_role", SPEAKER_ROLES),
)
