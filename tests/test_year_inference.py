"""
Unit tests for infer_doc_year — the download-time year inference helper.

Pure-function tests — no I/O, no DB, no settings. Cover:
  - URL path year (.../2025/minutes.pdf)
  - filename year (FY2024_budget.pdf, 2026-01-15-minutes.pdf)
  - source_page_url fallback when media URL has no year
  - ambiguous multi-year URL returns the earliest (conservative)
  - no year anywhere returns None
  - false-positive guard: 12345 in path does not match
  - query strings do not lead to false matches
  - parent_candidate_years used only when exactly one year is listed

Run:
    poetry run pytest tests/test_year_inference.py -v
"""

from __future__ import annotations

from app.services.web_scraper._year_inference import infer_doc_year


# ---------------------------------------------------------------------------
# URL path year
# ---------------------------------------------------------------------------


def test_url_path_year_simple():
    assert (
        infer_doc_year(
            url="https://www.example.com/board/2025/minutes.pdf",
            filename=None,
            source_page_url=None,
        )
        == 2025
    )


def test_url_path_year_with_month_subpath():
    assert (
        infer_doc_year(
            url="https://www.example.com/meeting-archives/2024/01/minutes.pdf",
            filename=None,
            source_page_url=None,
        )
        == 2024
    )


# ---------------------------------------------------------------------------
# Filename year
# ---------------------------------------------------------------------------


def test_filename_year_prefix():
    assert (
        infer_doc_year(
            url="https://www.example.com/download/abc123",
            filename="FY2024_budget.pdf",
            source_page_url=None,
        )
        == 2024
    )


def test_filename_year_iso_date():
    assert (
        infer_doc_year(
            url="https://www.example.com/files/uuid",
            filename="2026-01-15-minutes.pdf",
            source_page_url=None,
        )
        == 2026
    )


def test_filename_falls_back_to_url_filename_segment():
    # No explicit filename; URL's last path segment carries the year.
    assert (
        infer_doc_year(
            url="https://www.example.com/files/2023-agenda.pdf",
            filename=None,
            source_page_url=None,
        )
        == 2023
    )


# ---------------------------------------------------------------------------
# Source page fallback
# ---------------------------------------------------------------------------


def test_source_page_url_used_when_media_url_has_no_year():
    assert (
        infer_doc_year(
            url="https://www.example.com/download/uuid-file.pdf",
            filename=None,
            source_page_url="https://www.example.com/meeting-archives/2024/",
        )
        == 2024
    )


# ---------------------------------------------------------------------------
# Ambiguous / conservative
# ---------------------------------------------------------------------------


def test_ambiguous_two_years_returns_earliest():
    # A 2024 page linking a 2023 doc — conservative pick is the earlier year.
    assert (
        infer_doc_year(
            url="https://www.example.com/2024/agendas/2023-01-15.pdf",
            filename=None,
            source_page_url=None,
        )
        == 2023
    )


# ---------------------------------------------------------------------------
# No year anywhere
# ---------------------------------------------------------------------------


def test_no_year_anywhere_returns_none():
    assert (
        infer_doc_year(
            url="https://www.example.com/board/minutes/agenda.pdf",
            filename="agenda.pdf",
            source_page_url="https://www.example.com/board/minutes/",
        )
        is None
    )


# ---------------------------------------------------------------------------
# False-positive guards
# ---------------------------------------------------------------------------


def test_five_digit_id_is_not_a_year():
    assert (
        infer_doc_year(
            url="https://www.example.com/files/12345/data.pdf",
            filename=None,
            source_page_url=None,
        )
        is None
    )


def test_query_string_does_not_introduce_false_year():
    assert (
        infer_doc_year(
            url="https://www.example.com/download?token=abc12345def",
            filename=None,
            source_page_url=None,
        )
        is None
    )


# ---------------------------------------------------------------------------
# parent_candidate_years
# ---------------------------------------------------------------------------


def test_parent_candidate_single_year_used_as_last_resort():
    assert (
        infer_doc_year(
            url="https://www.example.com/download/uuid.pdf",
            filename="document.pdf",
            source_page_url="https://www.example.com/files/",
            parent_candidate_years=[2025],
        )
        == 2025
    )


def test_parent_candidate_multiple_years_not_used():
    # Two candidate years = ambiguous, do not pick one.
    assert (
        infer_doc_year(
            url="https://www.example.com/download/uuid.pdf",
            filename="document.pdf",
            source_page_url="https://www.example.com/files/",
            parent_candidate_years=[2024, 2025],
        )
        is None
    )


def test_parent_candidate_loses_to_url_year():
    # URL year wins even if parent_candidate_years is set.
    assert (
        infer_doc_year(
            url="https://www.example.com/2026/minutes.pdf",
            filename=None,
            source_page_url=None,
            parent_candidate_years=[2024],
        )
        == 2026
    )


def test_parent_candidate_empty_list_ignored():
    assert (
        infer_doc_year(
            url="https://www.example.com/download/uuid.pdf",
            filename="document.pdf",
            source_page_url=None,
            parent_candidate_years=[],
        )
        is None
    )
