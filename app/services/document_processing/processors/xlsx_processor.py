"""XLSX / XLS document processor with table-aware chunking."""

import logging
from typing import Any

try:
    import openpyxl
    from openpyxl.utils import get_column_letter
except ImportError:
    openpyxl = None

from app.services.document_processing.base import DocumentProcessor

logger = logging.getLogger(__name__)

# How many data rows to include in each chunk (headers are always repeated).
_DEFAULT_ROWS_PER_CHUNK = 25

# openpyxl loads .xls as well via xlrd compatibility, but we guard the import.
_SUPPORTED_EXTENSIONS = [".xlsx", ".xls"]
_SUPPORTED_MIME_TYPES = [
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.ms-excel",
]


def _cell_value(cell) -> str:
    """Coerce an openpyxl cell value to a clean string safe for Markdown tables."""
    v = cell.value
    if v is None:
        return ""
    text = str(v).strip()
    # Escape pipe characters so they don't break the Markdown table syntax.
    return text.replace("|", "\\|").replace("\n", " ")


def _make_separator(col_count: int) -> str:
    return "| " + " | ".join(["---"] * col_count) + " |"


def _row_to_markdown(values: list[str]) -> str:
    return "| " + " | ".join(values) + " |"


def _find_header_row_index(sheet) -> int:
    """Return the 0-based index of the first row that has at least two non-empty cells."""
    for idx, row in enumerate(sheet.iter_rows()):
        non_empty = sum(1 for c in row if c.value not in (None, ""))
        if non_empty >= 2:
            return idx
    return 0


def _sheet_to_markdown(sheet) -> tuple[str, list[str], list[list[str]]]:
    """
    Convert one worksheet into:
        - full_markdown  : the complete markdown table string
        - headers        : list of header strings
        - data_rows      : list of rows, each a list of cell strings

    Title-like rows above the header (single non-empty cell across the full
    width or merged-cell spans) are prepended as plain text above the table.
    """
    all_rows = list(sheet.iter_rows())
    if not all_rows:
        return "", [], []

    header_idx = _find_header_row_index(sheet)

    # Collect any title text sitting above the header row.
    title_lines: list[str] = []
    for row in all_rows[:header_idx]:
        values = [_cell_value(c) for c in row]
        non_empty = [v for v in values if v]
        if non_empty:
            title_lines.append(" ".join(non_empty))

    header_row = all_rows[header_idx]
    headers = [_cell_value(c) for c in header_row]
    col_count = len(headers)

    data_rows: list[list[str]] = []
    for row in all_rows[header_idx + 1 :]:
        values = [_cell_value(c) for c in row]
        # Pad / trim to match header column count.
        values = (values + [""] * col_count)[:col_count]
        # Skip completely empty rows.
        if any(v for v in values):
            data_rows.append(values)

    lines: list[str] = []
    if title_lines:
        lines.extend(title_lines)
        lines.append("")

    lines.append(_row_to_markdown(headers))
    lines.append(_make_separator(col_count))
    for row in data_rows:
        lines.append(_row_to_markdown(row))

    return "\n".join(lines), headers, data_rows


class XLSXProcessor(DocumentProcessor):
    """Process Excel workbooks (.xlsx / .xls) into Markdown table text."""

    supported_extensions = _SUPPORTED_EXTENSIONS
    supported_mime_types = _SUPPORTED_MIME_TYPES

    # ------------------------------------------------------------------
    # Public interface (DocumentProcessor ABC)
    # ------------------------------------------------------------------

    def extract_text(self, file_path: str) -> str:
        """
        Return the full workbook as Markdown tables, one section per sheet.

        This is used by the summarizer pipeline stage.  The text preserves
        every sheet's header row and all data rows so the LLM can read the
        complete document.
        """
        self._require_openpyxl()
        wb = openpyxl.load_workbook(file_path, read_only=True, data_only=True)

        sections: list[str] = []
        for sheet_name in wb.sheetnames:
            ws = wb[sheet_name]
            table_md, _, _ = _sheet_to_markdown(ws)
            if table_md.strip():
                sections.append(f"## Sheet: {sheet_name}\n\n{table_md}")

        wb.close()

        if not sections:
            return ""

        full_text = "\n\n---\n\n".join(sections)
        logger.info(
            f"Extracted {len(full_text)} chars from {len(sections)} sheet(s) in {file_path}"
        )
        return full_text

    def extract_metadata(self, file_path: str) -> dict[str, Any]:
        """Return workbook-level metadata (sheet names, row/col counts)."""
        self._require_openpyxl()
        try:
            wb = openpyxl.load_workbook(file_path, read_only=True, data_only=True)
            sheets_info: list[dict[str, Any]] = []
            total_rows = 0
            for sheet_name in wb.sheetnames:
                ws = wb[sheet_name]
                row_count = ws.max_row or 0
                col_count = ws.max_column or 0
                total_rows += row_count
                sheets_info.append(
                    {
                        "sheet_name": sheet_name,
                        "row_count": row_count,
                        "col_count": col_count,
                    }
                )
            wb.close()
            return {
                "sheet_count": len(sheets_info),
                "sheets": sheets_info,
                "total_row_count": total_rows,
                "document_format": "xlsx",
            }
        except Exception as exc:
            logger.error(f"Error extracting XLSX metadata from {file_path}: {exc}")
            return {"sheet_count": 0, "sheets": [], "total_row_count": 0}

    def validate(self, file_path: str) -> bool:
        """Return True if the file can be opened as a workbook."""
        if openpyxl is None:
            return False
        try:
            wb = openpyxl.load_workbook(file_path, read_only=True)
            wb.close()
            return True
        except Exception:
            return False

    # ------------------------------------------------------------------
    # XLSX-specific chunking
    # ------------------------------------------------------------------

    def chunk_spreadsheet(
        self,
        file_path: str,
        rows_per_chunk: int = _DEFAULT_ROWS_PER_CHUNK,
    ) -> list[dict[str, Any]]:
        """
        Chunk the workbook into row-group segments, repeating column headers
        at the top of every chunk.

        Returns a list of dicts:
            {
                "text":       <markdown string>,
                "sheet_name": <worksheet name>,
                "row_start":  <1-based first data row index in this chunk>,
                "row_end":    <1-based last data row index in this chunk>,
            }

        Strategy
        --------
        - Each sheet is treated independently.
        - The header row is detected automatically and repeated in every chunk.
        - Data rows are grouped into batches of `rows_per_chunk`.
        - Small sheets (total data rows <= rows_per_chunk) produce a single chunk.
        - Entirely empty sheets are skipped.
        """
        self._require_openpyxl()
        wb = openpyxl.load_workbook(file_path, read_only=True, data_only=True)

        chunks: list[dict[str, Any]] = []

        for sheet_name in wb.sheetnames:
            ws = wb[sheet_name]
            _, headers, data_rows = _sheet_to_markdown(ws)

            if not headers or not data_rows:
                logger.debug(f"Skipping empty sheet '{sheet_name}' in {file_path}")
                continue

            col_count = len(headers)
            header_line = _row_to_markdown(headers)
            separator_line = _make_separator(col_count)

            # Split data rows into groups.
            for batch_start in range(0, len(data_rows), rows_per_chunk):
                batch = data_rows[batch_start : batch_start + rows_per_chunk]
                row_start = batch_start + 1  # 1-based
                row_end = batch_start + len(batch)

                # Build chunk text.
                if len(data_rows) <= rows_per_chunk:
                    # Single-chunk sheet – no row range annotation.
                    heading = f"## Sheet: {sheet_name}"
                else:
                    heading = f"## Sheet: {sheet_name} (rows {row_start}–{row_end})"

                lines = [heading, "", header_line, separator_line]
                for row in batch:
                    lines.append(_row_to_markdown(row))

                chunks.append(
                    {
                        "text": "\n".join(lines),
                        "sheet_name": sheet_name,
                        "row_start": row_start,
                        "row_end": row_end,
                    }
                )

        wb.close()
        logger.info(
            f"Produced {len(chunks)} spreadsheet chunk(s) from {file_path}"
        )
        return chunks

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _require_openpyxl() -> None:
        if openpyxl is None:
            raise ImportError(
                "openpyxl is required for XLSX processing. "
                "Install it with: pip install openpyxl"
            )
