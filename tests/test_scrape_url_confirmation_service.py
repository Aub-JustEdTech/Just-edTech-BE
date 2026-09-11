"""Tests for JSON-backed scrape URL confirmation service."""

from __future__ import annotations

from types import SimpleNamespace

from app.services import school_scrape_url_confirmation_service as svc


def test_load_json_records_returns_20_schools():
    records = svc._load_json_records()
    assert len(records) == 20
    assert records[0]["org_code"] == "07530000"
    assert records[0]["candidates"]


def test_format_candidates_dedupes_and_ranks_by_score():
    raw = [
        {"url": "https://example.org/a", "score": 10, "matched_keywords": []},
        {"url": "https://example.org/b", "score": 100, "matched_keywords": ["board"]},
        {"url": "https://example.org/b/", "score": 50, "matched_keywords": []},
    ]
    out = svc._format_candidates(raw, max_candidates=5)
    assert len(out) == 2
    assert out[0].url == "https://example.org/b"
    assert out[0].score == 100
    assert out[0].rank == 1
    assert out[0].source == "discovered"
    assert out[1].rank == 2


def test_build_review_row_marks_not_added_without_scrape_url():
    record = {
        "name": "Test",
        "org_code": "00000000",
        "website": "https://example.org",
        "discovery_method": "test",
        "total_urls_scanned": 1,
        "candidates": [{"url": "https://example.org/minutes", "score": 90}],
    }
    review = svc._build_review_row(record, None, max_candidates=5)
    assert review.in_database is False
    assert review.has_confirmed_scrape_url is False
    assert review.total_candidates == 1
    assert review.candidates[0].score == 90
    assert review.candidates[0].is_selected is False


def test_format_candidates_merges_manual_scrape_urls_and_marks_selected():
    raw = [
        {"url": "https://example.org/discovered", "score": 80, "matched_keywords": []},
    ]
    school = SimpleNamespace(
        scrape_url_id=7,
        scrape_urls=[
            SimpleNamespace(
                id=7,
                url="https://example.org/manual-custom",
                is_active=True,
            ),
            SimpleNamespace(
                id=8,
                url="https://example.org/discovered",
                is_active=True,
            ),
        ],
    )
    out = svc._format_candidates(raw, max_candidates=10, school=school)
    assert len(out) == 2
    # Selected manual URL is sorted to the top.
    assert out[0].url == "https://example.org/manual-custom"
    assert out[0].source == "manual"
    assert out[0].is_selected is True
    assert out[0].scrape_url_id == 7
    assert out[1].url == "https://example.org/discovered"
    assert out[1].source == "discovered"
    assert out[1].is_selected is False
    assert out[1].scrape_url_id == 8
