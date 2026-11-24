"""Unit tests for agentic RAG corpus fallback helpers (no reclassify)."""

from __future__ import annotations

from app.services.agentic_rag.fallback import (
    resolve_keyword_flags,
    resolve_semantic_query,
)


def test_resolve_keyword_flags_infers_sexed_pack():
    flags = resolve_keyword_flags(topic_categories=["sexed"])
    assert flags is not None
    assert "CHPE Framework" in flags
    assert "Get Real" in flags
    assert "opt-out" in flags


def test_resolve_keyword_flags_merges_explicit_first():
    flags = resolve_keyword_flags(
        keyword_flags=["custom-flag"],
        topic_categories=["sexed"],
    )
    assert flags[0] == "custom-flag"
    assert "CHPE Framework" in flags


def test_resolve_semantic_query_prefers_explicit():
    assert (
        resolve_semantic_query(
            semantic_query="  custom query  ",
            topic_categories=["sexed"],
        )
        == "custom query"
    )


def test_resolve_semantic_query_infers_from_sexed_category():
    q = resolve_semantic_query(topic_categories=["sexed"])
    assert q is not None
    assert "CHPE" in q
    assert "sex education" in q.lower()


def test_resolve_semantic_query_infers_from_book_action():
    q = resolve_semantic_query(action_types=["book_challenged"])
    assert q is not None
    assert "book challenge" in q.lower()
