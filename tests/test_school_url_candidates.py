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


def test_preserve_query_keeps_distinct_cms_pages():
    raw = [
        {
            "url": "https://example.com/apps/pages/index.jsp?uREC_ID=1&pREC_ID=100",
            "score": 100,
            "matched_keywords": [],
        },
        {
            "url": "https://example.com/apps/pages/index.jsp?uREC_ID=1&pREC_ID=200",
            "score": 90,
            "matched_keywords": [],
        },
        {
            "url": "https://example.com/apps/pages/index.jsp?uREC_ID=1&pREC_ID=100#nav",
            "score": 50,
            "matched_keywords": [],
        },
    ]
    collapsed = dedupe_and_rank_candidates(raw, max_candidates=10)
    assert len(collapsed) == 1

    kept = dedupe_and_rank_candidates(raw, max_candidates=10, preserve_query=True)
    assert len(kept) == 2
    assert kept[0]["url"].endswith("pREC_ID=100")
    assert kept[1]["url"].endswith("pREC_ID=200")
