"""Unit tests for the V1 taxonomy schema extensions.

Pure-function tests — no I/O, no DB. Cover:
  - DocClassification accepts meeting_doc_type + meeting_body
  - DocClassification rejects invalid meeting_doc_type / meeting_body
  - ChunkClassification accepts topic_tags, action_stage, speakers
  - ChunkClassification rejects invalid action_stage / speaker roles
  - Fallback / null behavior

Run:
    poetry run pytest tests/test_taxonomy_v1.py -v
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.services.heatmap_ingest.taxonomy import (
    ACTION_STAGES,
    MEETING_BODIES,
    MEETING_DOC_TYPES,
    SPEAKER_ROLES,
    ChunkClassification,
    DocClassification,
    Speaker,
    TopicTag,
)


# ---------------------------------------------------------------------------
# DocClassification (doc-level)
# ---------------------------------------------------------------------------


def test_doc_classification_accepts_v1_fields():
    d = DocClassification(
        entity_type="board_minutes",
        doc_kind="minutes",
        meeting_date="2024-03-14",
        meeting_doc_type="Minutes",
        meeting_body="Full Board",
    )
    assert d.meeting_doc_type == "Minutes"
    assert d.meeting_body == "Full Board"


def test_doc_classification_v1_fields_optional():
    d = DocClassification(
        entity_type="board_minutes",
        doc_kind="minutes",
    )
    assert d.meeting_doc_type is None
    assert d.meeting_body is None


def test_doc_classification_rejects_invalid_meeting_doc_type():
    with pytest.raises(ValidationError):
        DocClassification(
            entity_type="board_minutes",
            doc_kind="minutes",
            meeting_doc_type="NotARealType",
        )


def test_doc_classification_rejects_invalid_meeting_body():
    with pytest.raises(ValidationError):
        DocClassification(
            entity_type="board_minutes",
            doc_kind="minutes",
            meeting_body="Steering Committee",
        )


def test_doc_classification_enums_match_spec():
    assert MEETING_DOC_TYPES == (
        "Minutes",
        "Agenda",
        "Agenda Attachment",
        "Public Comment Transcript",
        "Policy Document",
        "Presentation Slide",
    )
    assert MEETING_BODIES == (
        "Full Board",
        "Curriculum Subcommittee",
        "Policy Subcommittee",
        "Public Hearing",
        "Special Meeting",
    )


# ---------------------------------------------------------------------------
# ChunkClassification (chunk-level)
# ---------------------------------------------------------------------------


def test_chunk_classification_accepts_v1_fields():
    c = ChunkClassification(
        topic_tags=[
            TopicTag(category="sexed", subtopic="comprehensive"),
            TopicTag(category="governance", subtopic="vote_recorded"),
        ],
        action_stage="Vote — Passed",
        speakers=[
            Speaker(name="Sarah Chen", role="Board Member"),
            Speaker(name="Dr. Reyes", role="Superintendent/Admin"),
        ],
    )
    assert len(c.topic_tags) == 2
    assert c.action_stage == "Vote — Passed"
    assert c.speakers[0].role == "Board Member"


def test_chunk_classification_v1_fields_default_empty():
    c = ChunkClassification()
    assert c.topic_tags == []
    assert c.action_stage is None
    assert c.speakers == []


def test_chunk_classification_rejects_invalid_action_stage():
    with pytest.raises(ValidationError):
        ChunkClassification(action_stage="NotAStage")


def test_chunk_classification_rejects_invalid_speaker_role():
    with pytest.raises(ValidationError):
        ChunkClassification(
            speakers=[Speaker(name="X", role="Principal")]
        )


def test_chunk_classification_action_stage_enum_count():
    # Spec A2 lists 10 action_stage values.
    assert len(ACTION_STAGES) == 10


def test_chunk_classification_speaker_role_enum_count():
    # Spec A2 lists 5 speaker_role values.
    assert len(SPEAKER_ROLES) == 5
