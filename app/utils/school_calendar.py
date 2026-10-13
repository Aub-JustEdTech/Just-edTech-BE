"""
US K-12 academic calendar derivation helpers.

Used by the heatmap ingest pipeline to derive `school_year` and
`quarter_month` from a `meeting_date` (per the Heatmap Ingest Metadata v1
plan). The academic-year cutoff is Aug–Jul: a meeting on 2024-07-15 belongs
to school year "2023-2024"; a meeting on 2024-08-20 belongs to "2024-2025".
"""

from __future__ import annotations

from datetime import date


_ACADEMIC_YEAR_START_MONTH = 8  # August


def derive_school_year(meeting_date: date | None) -> str | None:
    """Return a school-year string like "2023-2024" for the given date.

    Uses an August–July academic year cutoff. Returns None for None input.
    """
    if meeting_date is None:
        return None
    year = meeting_date.year
    if meeting_date.month < _ACADEMIC_YEAR_START_MONTH:
        start_year = year - 1
    else:
        start_year = year
    return f"{start_year}-{start_year + 1}"


def derive_quarter_month(meeting_date: date | None) -> str | None:
    """Return a `YYYY-MM` quarter_month string for the given date.

    Per spec A2 the field is also acceptable as `YYYY-Qn`, but `YYYY-MM` is
    preferred because it's a strict superset (finer granularity) and lets
    the retrieval side collapse to quarters trivially. Returns None for None.
    """
    if meeting_date is None:
        return None
    return f"{meeting_date.year:04d}-{meeting_date.month:02d}"
