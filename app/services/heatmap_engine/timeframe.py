"""
Timeframe-to-filter translation for the Heatmap Generation Engine.

Each `TimeframePreset` is converted into a `must_match_any` fragment
(`{field: [values]}`) that the vector store can apply directly against
Qdrant chunk payload. Presets snap to academic-year buckets derived from
the August–July cutoff in `app/utils/school_calendar.py`:

- `month` / `last_2_months` / `quarter` filter on `quarter_month` (YYYY-MM)
- `year` / `2_years` / `3_years` filter on `school_year` ("YYYY-YYYY")
"""

from __future__ import annotations

from datetime import date
from typing import Any

from app.schemas.heatmap_engine import TimeframePreset
from app.utils.school_calendar import derive_quarter_month, derive_school_year

# Field name on Qdrant chunk payloads holding the YYYY-MM bucket.
_QUARTER_MONTH_FIELD = "quarter_month"
# Field name on Qdrant chunk payloads holding the academic-year string.
_SCHOOL_YEAR_FIELD = "school_year"
# Field name on Qdrant chunk payloads holding the per-chunk meeting date,
# indexed as DATETIME (see `qdrant_store._ensure_payload_indexes`) so it
# supports a native `Range` condition for the custom date-range filter.
_MEETING_DATE_FIELD = "meeting_date"


def previous_school_year(school_year: str) -> str:
    """Return the academic year preceding the given one.

    `"2025-2026"` -> `"2024-2025"`.
    """
    start_year = int(school_year.split("-")[0])
    return f"{start_year - 1}-{start_year}"


def _previous_quarter_months(anchor: date, count: int) -> list[str]:
    """Return the `count` quarter_month buckets ending at `anchor`'s month.

    Months step backward in calendar time (most recent first). None values
    (which only occur if `anchor` is None — not possible here) are skipped.
    """
    values: list[str] = []
    year, month = anchor.year, anchor.month
    for _ in range(count):
        qm = derive_quarter_month(date(year, month, 1))
        if qm is not None:
            values.append(qm)
        # Step back one month.
        month -= 1
        if month < 1:
            month = 12
            year -= 1
    return values


def build_timeframe_filter(
    preset: TimeframePreset,
    *,
    anchor: date | None = None,
) -> dict[str, list[Any]]:
    """Translate a `TimeframePreset` into a `must_match_any` filter fragment.

    Returns a dict like `{"school_year": ["2025-2026"]}` or
    `{"quarter_month": ["2026-07", "2026-06"]}`. An empty dict would mean
    "no filter"; this function never returns an empty dict for a valid
    preset — callers can rely on the returned fragment being non-empty.
    """
    today = anchor or date.today()

    if preset == TimeframePreset.MONTH:
        qm = derive_quarter_month(today)
        return {_QUARTER_MONTH_FIELD: [qm]} if qm else {}

    if preset == TimeframePreset.LAST_2_MONTHS:
        values = _previous_quarter_months(today, 2)
        return {_QUARTER_MONTH_FIELD: values} if values else {}

    if preset == TimeframePreset.QUARTER:
        values = _previous_quarter_months(today, 3)
        return {_QUARTER_MONTH_FIELD: values} if values else {}

    current_sy = derive_school_year(today)
    if current_sy is None:
        return {}

    if preset == TimeframePreset.YEAR:
        return {_SCHOOL_YEAR_FIELD: [current_sy]}

    if preset == TimeframePreset.TWO_YEARS:
        return {_SCHOOL_YEAR_FIELD: [current_sy, previous_school_year(current_sy)]}

    if preset == TimeframePreset.THREE_YEARS:
        prev1 = previous_school_year(current_sy)
        prev2 = previous_school_year(prev1)
        return {_SCHOOL_YEAR_FIELD: [current_sy, prev1, prev2]}

    # Defensive — unknown preset applies no timeframe filter.
    return {}


def build_date_range_filter(
    start_date: date, end_date: date
) -> dict[str, dict[str, str]]:
    """Translate an explicit start/end date into a `range_match` fragment.

    Returns `{"meeting_date": {"gte": iso, "lte": iso}}` spanning
    midnight UTC on `start_date` through 23:59:59 UTC on `end_date`
    (inclusive on both ends). Day-level granularity, unlike the
    month/year-bucket presets above — this is the true custom date-range
    filter, matched via a native Qdrant `Range` condition rather than an
    enumerated bucket list.
    """
    return {
        _MEETING_DATE_FIELD: {
            "gte": f"{start_date.isoformat()}T00:00:00Z",
            "lte": f"{end_date.isoformat()}T23:59:59Z",
        }
    }
