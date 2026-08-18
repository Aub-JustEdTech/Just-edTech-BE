"""Shared helpers for rendering tabular data as Markdown tables.

Used by both `XLSXProcessor` (spreadsheet chunking) and `DocxProcessor`
(table-aware section extraction) so table rendering stays consistent without
either processor importing the other.
"""


def make_separator(col_count: int) -> str:
    return "| " + " | ".join(["---"] * col_count) + " |"


def row_to_markdown(values: list[str]) -> str:
    return "| " + " | ".join(values) + " |"


def cell_value(value) -> str:
    """Coerce a raw cell value to a clean string safe for Markdown tables."""
    if value is None:
        return ""
    text = str(value).strip()
    # Escape pipe characters so they don't break the Markdown table syntax.
    return text.replace("|", "\\|").replace("\n", " ")
