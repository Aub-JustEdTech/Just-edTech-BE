"""Unit tests for app.utils.school_calendar derivation helpers.

Pure-function tests — no I/O, no DB. Cover:
  - Academic year boundary (Aug 1 cutoff)
  - Year rollover (Jul 2024 -> 2023-2024; Aug 2024 -> 2024-2025)
  - quarter_month format YYYY-MM
  - None handling

Run:
    poetry run pytest tests/test_school_calendar.py -v
"""

from __future__ import annotations

from datetime import date

from app.utils.school_calendar import (
    derive_quarter_month,
    derive_school_year,
)


def test_school_year_july_belongs_to_prior_year():
    # July is still the prior academic year (Aug-Jul cutoff).
    assert derive_school_year(date(2024, 7, 15)) == "2023-2024"


def test_school_year_august_starts_new_year():
    assert derive_school_year(date(2024, 8, 1)) == "2024-2025"


def test_school_year_january_belongs_to_prior_year():
    assert derive_school_year(date(2025, 1, 10)) == "2024-2025"


def test_school_year_september():
    assert derive_school_year(date(2023, 9, 30)) == "2023-2024"


def test_school_year_none_returns_none():
    assert derive_school_year(None) is None


def test_quarter_month_format():
    assert derive_quarter_month(date(2024, 3, 14)) == "2024-03"
    assert derive_quarter_month(date(2025, 12, 1)) == "2025-12"
    assert derive_quarter_month(date(2026, 1, 7)) == "2026-01"


def test_quarter_month_none_returns_none():
    assert derive_quarter_month(None) is None
