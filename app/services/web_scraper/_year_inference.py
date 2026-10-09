"""
Year inference for the download-time year filter.

Pure function — no I/O, no DB, no settings lookup. The caller decides
what to do with the returned `int | None` (typically: skip if not in
`settings.SCHOOL_SCRAPER_ALLOWED_YEARS`).

Priority order (first non-None wins):
  1. 4-digit year in the media URL path
     (.../2025/.../minutes.pdf)
  2. 4-digit year in the filename
     (FY2024_budget.pdf, 2026-01-15-minutes.pdf)
  3. 4-digit year in the source_page_url
     (.../meeting-archives/2024/)
  4. Single-element parent_candidate_years (from discovery metadata
     data_years_available) — only used when exactly
     one year is listed, otherwise ambiguous.

The regex `(?<!\\d)(20\\d{2})(?!\\d)` matches 2000-2099 with no
surrounding digits, so `12345` does not falsely match as year 1234 or
2345. When multiple 4-digit years match in the same string, the
earliest is returned (conservative — older documents are more likely
to be in scope of a "past 3 years" window if both years appear, e.g.
a 2024 page linking a 2023 doc).
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

# Matches a standalone 4-digit year in the 2000-2099 range, not
# surrounded by other digits (so `12345` won't match).
_YEAR_RE = re.compile(r"(?<!\d)(20\d{2})(?!\d)")

# US-style short dates in filenames: 03-16-26, 4-6-26, 03/16/26, 7-10-2025
# Captures a trailing 2-digit or 4-digit year after a month-day prefix.
_SHORT_DATE_YEAR_RE = re.compile(
    r"(?<!\d)(?:0?[1-9]|1[0-2])[-/.](?:0?[1-9]|[12]\d|3[01])[-/.](20\d{2}|\d{2})(?!\d)"
)


def _year_from_short_date(text: str) -> int | None:
    """Infer year from ``MM-DD-YY`` / ``M-D-YYYY`` style date fragments."""
    years: list[int] = []
    for m in _SHORT_DATE_YEAR_RE.finditer(text or ""):
        raw = m.group(1)
        if len(raw) == 4:
            years.append(int(raw))
            continue
        yy = int(raw)
        # School docs are 2000s; map 00-99 → 2000-2099.
        years.append(2000 + yy)
    if not years:
        return None
    return min(years)


def _earliest_year(text: str) -> int | None:
    """Return the smallest 4-digit 20xx year in ``text``, or None."""
    years = [int(m.group(1)) for m in _YEAR_RE.finditer(text or "")]
    if not years:
        return _year_from_short_date(text)
    short = _year_from_short_date(text)
    if short is not None:
        years.append(short)
    return min(years)


def _filename_from_url(url: str) -> str:
    """Extract the last path segment of ``url`` (the filename)."""
    path = urlparse(url).path
    return path.rsplit("/", 1)[-1] if path else ""


def infer_doc_year(
    *,
    url: str,
    filename: str | None,
    source_page_url: str | None,
    parent_candidate_years: list[int] | None = None,
) -> int | None:
    """Infer a 4-digit document year from URL/filename/page context.

    Returns None if no unambiguous year could be extracted.
    """
    # 1. Media URL path (the whole path, not just the filename, so that
    #    .../board/2025/01/minutes.pdf still resolves to 2025).
    if url:
        year = _earliest_year(urlparse(url).path)
        if year is not None:
            return year

    # 2. Filename (covers Finalsite `data-file-name` hints where the
    #    href has no extension but the filename does, e.g. FY2024_budget.pdf).
    fname = filename or ""
    if fname:
        year = _earliest_year(fname)
        if year is not None:
            return year
    # 2b. Fall back to the URL's own filename segment if no explicit
    #     filename was passed.
    if not fname and url:
        year = _earliest_year(_filename_from_url(url))
        if year is not None:
            return year

    # 3. Source page URL path (e.g. .../meeting-archives/2024/).
    if source_page_url:
        year = _earliest_year(urlparse(source_page_url).path)
        if year is not None:
            return year

    # 4. parent_candidate_years — only when exactly one year is listed
    #    (otherwise the doc could belong to any of them).
    if parent_candidate_years and len(parent_candidate_years) == 1:
        return parent_candidate_years[0]

    return None
