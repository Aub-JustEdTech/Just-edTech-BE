"""Tests for XLSXProcessor.chunk_spreadsheet, focused on the char-cap fix:
a wide sheet (many columns) previously could produce an oversized chunk
despite staying under the row cap, since only row count was bounded.
"""

from __future__ import annotations

import openpyxl

from app.services.document_processing.processors.xlsx_processor import XLSXProcessor


def _write_sheet(tmp_path, headers, rows) -> str:
    wb = openpyxl.Workbook()
    ws = wb.active
    if headers:
        ws.append(headers)
    for row in rows:
        ws.append(row)
    path = tmp_path / "sheet.xlsx"
    wb.save(path)
    return str(path)


def test_narrow_sheet_uses_row_cap_unchanged(tmp_path):
    """Existing behavior: a narrow sheet under the row cap is one chunk."""
    headers = ["Name", "Vote"]
    rows = [[f"Member {i}", "Yes"] for i in range(10)]
    path = _write_sheet(tmp_path, headers, rows)

    chunks = XLSXProcessor().chunk_spreadsheet(path, rows_per_chunk=25)

    assert len(chunks) == 1
    assert chunks[0]["row_start"] == 1
    assert chunks[0]["row_end"] == 10
    assert "Sheet:" in chunks[0]["text"]


def test_narrow_sheet_splits_at_row_cap(tmp_path):
    headers = ["Name", "Vote"]
    rows = [[f"Member {i}", "Yes"] for i in range(30)]
    path = _write_sheet(tmp_path, headers, rows)

    chunks = XLSXProcessor().chunk_spreadsheet(path, rows_per_chunk=25)

    assert len(chunks) == 2
    assert (chunks[0]["row_start"], chunks[0]["row_end"]) == (1, 25)
    assert (chunks[1]["row_start"], chunks[1]["row_end"]) == (26, 30)


def test_wide_sheet_respects_char_cap_even_under_row_cap(tmp_path):
    """Regression test for the oversize bug: 25 rows of a 30-column sheet
    with long cell values must not land in a single oversized chunk."""
    headers = [f"Column {i}" for i in range(30)]
    long_value = "x" * 50
    rows = [[long_value] * 30 for _ in range(25)]
    path = _write_sheet(tmp_path, headers, rows)

    chunks = XLSXProcessor().chunk_spreadsheet(
        path, rows_per_chunk=25, max_chunk_chars=4000
    )

    assert len(chunks) > 1
    for chunk in chunks:
        assert len(chunk["text"]) <= 4000 + 1000  # header/separator overhead


def test_wide_sheet_row_ranges_are_contiguous_and_non_overlapping(tmp_path):
    headers = [f"Column {i}" for i in range(30)]
    long_value = "x" * 50
    rows = [[long_value] * 30 for _ in range(40)]
    path = _write_sheet(tmp_path, headers, rows)

    chunks = XLSXProcessor().chunk_spreadsheet(
        path, rows_per_chunk=25, max_chunk_chars=4000
    )

    assert chunks[0]["row_start"] == 1
    assert chunks[-1]["row_end"] == 40
    for earlier, later in zip(chunks, chunks[1:], strict=False):
        assert earlier["row_end"] + 1 == later["row_start"]


def test_empty_sheet_is_skipped(tmp_path):
    path = _write_sheet(tmp_path, [], [])
    chunks = XLSXProcessor().chunk_spreadsheet(path)
    assert chunks == []
