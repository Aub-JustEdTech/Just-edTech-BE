"""Pack heading-delimited sections (DOCX, Markdown) into size-bounded chunks.

Shared by `DocxProcessor.extract_sections` and the Markdown heading parser
so the "oversized section" and "no headings" packing logic is written and
tested once. Sections are never merged across different `heading_path`s —
this keeps `heading_path` unambiguous per chunk, at the cost of possibly
many small chunks for documents with many short sections.
"""

from app.services.document_processing.chunker import Chunker


def chunk_sections_by_heading(
    sections: list[dict],
    chunk_size: int,
    chunk_overlap: int,
) -> list[dict]:
    """
    Turn a list of `{"heading_path": str, "text": str, "is_table": bool}`
    sections (in document order) into a list of size-bounded chunks with the
    same shape.

    Each input section becomes one or more output chunks:
      - A section within `chunk_size` becomes exactly one chunk.
      - An oversized section is sub-split via the sentence-aware chunker,
        with every piece keeping the section's `heading_path`/`is_table`.
      - An empty/whitespace-only section is skipped.
    """
    chunker = Chunker(chunk_size=chunk_size, chunk_overlap=chunk_overlap)
    packed: list[dict] = []

    for section in sections:
        text = (section.get("text") or "").strip()
        if not text:
            continue

        heading_path = section.get("heading_path", "")
        is_table = section.get("is_table", False)

        if len(text) <= chunk_size:
            packed.append(
                {"text": text, "heading_path": heading_path, "is_table": is_table}
            )
            continue

        for piece in chunker.chunk_text(text, strategy="sentence"):
            packed.append(
                {"text": piece, "heading_path": heading_path, "is_table": is_table}
            )

    return packed
