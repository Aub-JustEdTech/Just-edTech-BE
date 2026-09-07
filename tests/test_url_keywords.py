"""Unit tests for app.services.web_scraper.url_keywords.

Run:
    poetry run pytest tests/test_url_keywords.py -v
"""

from __future__ import annotations

from app.services.web_scraper.url_keywords import (
    is_meeting_related_media,
    is_meeting_related_text,
    is_meeting_related_url,
)

# ---------------------------------------------------------------------------
# is_meeting_related_text — plain text (video title / filename), not a URL
# ---------------------------------------------------------------------------


def test_strong_keyword_in_title_matches():
    assert is_meeting_related_text("2026 Board Meeting-Minutes") is True


def test_hyphenated_strong_keyword_matches_natural_language_title():
    """The keyword list is written with hyphens ('school-committee'), but a
    real YouTube title is natural language with spaces — normalization must
    bridge the two."""
    assert is_meeting_related_text("School Committee Meeting - March 2026") is True


def test_underscore_keyword_matches_spaced_title():
    assert is_meeting_related_text("Board of Trustees Meeting") is True


def test_case_insensitive():
    assert is_meeting_related_text("SCHOOL COMMITTEE meeting") is True
    assert is_meeting_related_text("school committee MEETING") is True


def test_single_weak_keyword_is_not_enough():
    # "board" alone is too generic (e.g. a game night, a job posting).
    assert is_meeting_related_text("Board Game Night") is False


def test_two_weak_keywords_together_are_enough():
    assert is_meeting_related_text("Board and Committee Update") is True


def test_unrelated_title_does_not_match():
    assert is_meeting_related_text("Back to School 2026") is False


def test_empty_text_does_not_match():
    assert is_meeting_related_text("") is False
    assert is_meeting_related_text(None) is False  # type: ignore[arg-type]


def test_slash_prefixed_keyword_matches_without_slash():
    # "/agenda" and "/agendas" are strong (unambiguous even on a school
    # site), and path-only markers — text has no path structure, so the
    # leading "/" is stripped for the comparison.
    assert is_meeting_related_text("March Agenda") is True
    assert is_meeting_related_text("2026 Agendas") is True


# ---------------------------------------------------------------------------
# is_meeting_related_media — any one of page/media URL or title matching
# ---------------------------------------------------------------------------


def test_media_matches_via_page_url_only():
    assert (
        is_meeting_related_media(
            page_url="https://example.com/school-committee/",
            media_url="https://cdn.example.com/video123.mp4",
            title="Video",
        )
        is True
    )


def test_media_matches_via_title_only():
    assert (
        is_meeting_related_media(
            page_url="https://example.com/news/",
            media_url="https://cdn.example.com/video123.mp4",
            title="School Committee Meeting",
        )
        is True
    )


def test_media_matches_via_media_url_only():
    assert (
        is_meeting_related_media(
            page_url="https://example.com/news/",
            media_url="https://cdn.example.com/school-committee/video.mp4",
            title="Video",
        )
        is True
    )


def test_media_no_match_when_nothing_qualifies():
    assert (
        is_meeting_related_media(
            page_url="https://example.com/news/",
            media_url="https://cdn.example.com/video123.mp4",
            title="Back to School 2026",
        )
        is False
    )


def test_media_all_fields_optional():
    assert is_meeting_related_media() is False


# ---------------------------------------------------------------------------
# Sanity: is_meeting_related_url behavior is unchanged (regression guard)
# ---------------------------------------------------------------------------


def test_url_matching_still_works_as_before():
    assert is_meeting_related_url("https://example.com/school-committee/agendas/") is True
    assert is_meeting_related_url("https://example.com/staff/board-of-directors") is False
