"""Unit tests for school URL candidate deduplication."""

from app.utils.school_url_candidates import dedupe_and_rank_candidates


def test_dedupe_strips_fragments_and_limits_top_n():
    raw = [
        {
            "url": "https://example.com/minutes",
            "matched_keywords": ["minutes"],
            "score": 2,
        },
        {
            "url": "https://example.com/minutes#mobile-nav",
            "matched_keywords": ["minutes"],
            "score": 3,
        },
        {
            "url": "https://example.com/board",
            "matched_keywords": ["board"],
            "score": 1,
        },
        {
            "url": "https://example.com/agenda",
            "matched_keywords": ["agenda"],
            "score": 4,
        },
    ]

    result = dedupe_and_rank_candidates(raw, max_candidates=2)

    assert len(result) == 2
    assert result[0]["url"] == "https://example.com/agenda"
    assert result[1]["url"] == "https://example.com/minutes#mobile-nav"
    assert all("url_hash" in row for row in result)
