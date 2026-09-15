"""
Universal core topic taxonomy (state-agnostic), per spec A3a.

Five categories, each with a set of subtopics. A chunk may carry multiple
topic tags (per A9). The classifier emits `topic_category` + `topic_subtopic`
flat pairs so they become native facets in Qdrant payload + Postgres with
no string parsing.

State-specific curricula, statutes, and named advocacy orgs live in
state packs (see ma.py) and are injected into the prompt, NOT merged into
this taxonomy at the data layer.
"""

from __future__ import annotations

from typing import NamedTuple


class TopicSubtopic(NamedTuple):
    """A single `(category, subtopic)` taxonomy pair."""

    category: str
    subtopic: str
    description: str


class TopicCategory(NamedTuple):
    """A category grouping with its subtopics."""

    category: str
    description: str
    subtopics: tuple[TopicSubtopic, ...]


def _sub(category: str, subtopic: str, description: str) -> TopicSubtopic:
    return TopicSubtopic(
        category=category, subtopic=subtopic, description=description
    )


# ── Sex Education Policy ─────────────────────────────────────────────────────
SEX_ED = TopicCategory(
    category="sexed",
    description="Sex education policy and curriculum changes.",
    subtopics=(
        _sub("sexed", "comprehensive", "Comprehensive sex education."),
        _sub("sexed", "abstinence_only", "Abstinence-only instruction."),
        _sub("sexed", "abstinence_plus", "Abstinence-plus instruction."),
        _sub("sexed", "sexual_risk_avoidance", "Sexual risk avoidance."),
        _sub("sexed", "opt_in_policy", "Opt-in enrollment policy."),
        _sub("sexed", "opt_out_policy", "Opt-out enrollment policy."),
        _sub("sexed", "parental_notification", "Parental notification policy."),
        # Dotted names match prompt.py TOPIC_TAGS closed vocabulary (literal
        # strings — "change.expansion" is one subtopic, not a nested path).
        _sub("sexed", "change.expansion", "Curriculum expansion."),
        _sub("sexed", "change.reduction", "Curriculum reduction."),
        _sub("sexed", "change.under_review", "Curriculum under review."),
        _sub("sexed", "public_comment", "Public comment on sex ed policy."),
    ),
)

# ── LGBTQ+ Student Rights ────────────────────────────────────────────────────
LGBTQ = TopicCategory(
    category="lgbtq",
    description="LGBTQ+ student rights, protections, and inclusion policy.",
    subtopics=(
        _sub("lgbtq", "transgender_student_policy", "Transgender student policy."),
        _sub("lgbtq", "gender_identity_discussion", "Gender identity discussion."),
        _sub("lgbtq", "protections_adopted", "Protections adopted."),
        _sub("lgbtq", "pronoun_policy", "Pronoun policy."),
        _sub("lgbtq", "facilities_bathroom_policy", "Facilities / bathroom policy."),
        _sub("lgbtq", "athletics_participation", "Athletics participation policy."),
        _sub("lgbtq", "antidiscrimination_update", "Anti-discrimination update."),
    ),
)

# ── Curriculum Censorship & Book Challenges ──────────────────────────────────
CENSORSHIP = TopicCategory(
    category="censorship",
    description="Curriculum censorship and book/library material challenges.",
    subtopics=(
        _sub("censorship", "book_challenge_filed", "Book challenge filed."),
        _sub("censorship", "book_removed", "Book removed."),
        _sub("censorship", "book_retained", "Book retained."),
        _sub("censorship", "curriculum_material_challenge", "Curriculum material challenge."),
        _sub("censorship", "parental_rights_policy", "Parental rights policy."),
        _sub("censorship", "library_collection_policy", "Library collection policy."),
    ),
)

# ── Board Governance (factual events only — no stance in V1) ──────────────────
GOVERNANCE = TopicCategory(
    category="governance",
    description=(
        "Board governance factual events only — no stance in V1. "
        "Flags that a position was voiced or a vote recorded, without "
        "attaching direction. Stance/sentiment is a V2 pass."
    ),
    subtopics=(
        _sub(
            "governance",
            "member_position_stated",
            "Event flag only — a position was voiced, no direction attached until V2.",
        ),
        _sub(
            "governance",
            "vote_recorded",
            "Paired with action_stage = Vote.",
        ),
    ),
)

# ── Advocacy & Organizing Activity ───────────────────────────────────────────
ADVOCACY = TopicCategory(
    category="advocacy",
    description="Advocacy and organizing activity by external organizations.",
    subtopics=(
        _sub(
            "advocacy",
            "external_org_mentioned",
            "Paired with org_id / org_name (state pack).",
        ),
        _sub("advocacy", "presentation_or_testimony", "Presentation or testimony given."),
        _sub("advocacy", "petition_or_campaign_referenced", "Petition or campaign referenced."),
    ),
)

CORE_TOPIC_TAXONOMY: tuple[TopicCategory, ...] = (
    SEX_ED,
    LGBTQ,
    CENSORSHIP,
    GOVERNANCE,
    ADVOCACY,
)
"""Universal (state-agnostic) topic categories from spec A3a."""


class VocabPack(NamedTuple):
    """
    A fully merged vocabulary pack for a given state.

    `topic_taxonomy` is always the universal core; state-specific curricula
    and named advocacy orgs are surfaced to the classifier via
    `state_curricula` and `state_orgs` (kept separate so they can be injected
    into the prompt without mutating the taxonomy data).
    """

    state: str
    topic_taxonomy: tuple[TopicCategory, ...]
    state_curricula: tuple[TopicSubtopic, ...]
    state_orgs: tuple[str, ...]
    keyword_flags: tuple[str, ...]
