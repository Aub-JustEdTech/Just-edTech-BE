# Structured Document Chunking — Handover

Branch: `chore_structured-chunking-handover` (WIP, pushed as a snapshot of
`feat_structured-document-chunking`). Not merge-ready — no PR opened yet.
All 26 new unit tests pass (`poetry run pytest tests/test_chunker.py
tests/test_docx_chunking.py tests/test_markdown_chunking.py
tests/test_xlsx_chunking.py`), but there is no pipeline-level/integration
test and no manual run through `document_pipeline.py` end-to-end yet.

## Problem we were solving

The generic `Chunker` (`app/services/document_processing/chunker.py`) chunks
plain text with no awareness of document structure. For DOCX/Markdown board
policy documents this threw away heading context (no way to cite "which
section did this answer come from") and, for DOCX specifically, flattened
tables into the paragraph stream out of document order (python-docx's
`.paragraphs` and `.tables` are separate, order-losing collections).

## Decisions made

1. **Heading-aware chunking, shared between DOCX and Markdown.**
   `DocxProcessor.extract_sections()` (new) and `parse_markdown_sections()`
   (new, `markdown_heading_parser.py`) both produce the same shape:
   `{"heading_path": str, "text": str, "is_table": bool}`, in document order.
   A shared packer, `chunk_sections_by_heading()`
   (`heading_chunker.py`), turns those sections into size-bounded chunks:
   - A section that fits in `chunk_size` becomes exactly one chunk.
   - An oversized section is sub-split with the sentence-aware chunker,
     every piece keeping the parent section's `heading_path`/`is_table`.
   - Sections are **never merged across different `heading_path`s** — every
     chunk has one unambiguous heading path, at the cost of possibly many
     small chunks for documents with lots of short sections. This tradeoff
     was accepted deliberately; revisit if small-chunk volume becomes a
     real cost/quality problem.

2. **DOCX tables become their own chunk, rendered as Markdown**, instead of
   being flattened into surrounding paragraph text. Table rendering
   (`row_to_markdown`/`make_separator`/`cell_value`) was pulled out of
   `xlsx_processor.py` into a shared `markdown_table.py` so DOCX and XLSX
   don't duplicate (or import each other for) the same three functions.

3. **Markdown heading parsing is a hand-rolled regex, not a Markdown
   library.** Deliberate — the project has no markdown/mistune/markdown-it-py
   dependency, and all that's needed is ATX heading boundaries
   (`^#{1,6}\s`). It is **gated strictly on file extension** (`.md`/
   `.markdown`) in the pipeline, not on content — a `.txt` file with
   `#`-prefixed lines (issue references, hashtags) must never be misparsed
   as document structure. Do not reuse this parser for `.txt`/`.text`.

4. **PPTX wired into the pipeline** (slide-per-chunk, mirroring the existing
   PDF per-page branch) — `page_number` metadata key is reused for slide
   index rather than adding a `slide_number` key, since every downstream
   consumer treats it as an opaque per-chunk locator and the one
   PDF-specific use already gates on `document_type == ".pdf"` first.
   `PPTXProcessor` itself already existed on `main`; this branch only adds
   `.pptx` to `ALLOWED_DOCUMENT_TYPES` and the step3 chunking branch. No new
   PPTX-specific tests were added — it rides on the existing processor and
   the generic per-slide sentence chunker.

5. **XLSX chunk boundary gets a char cap alongside the existing row cap**
   (`_DEFAULT_MAX_CHUNK_CHARS = 4000`) — a wide sheet (many columns) could
   produce an oversized chunk while staying under the row limit. A batch
   closes on whichever limit hits first.

6. **Generic chunking now defaults to `strategy="sentence"`** everywhere
   except one deliberate holdout: `school_scraper` documents stay on
   `strategy="fixed"` + `mode="token"`, because they bill by token downstream
   (classifier + embedding model) and `_chunk_by_sentence`/
   `_chunk_by_paragraph` ignore `mode` entirely and size by `len()`. This is
   called out with an explicit comment in `document_pipeline.py` — **do not
   "normalize" this branch away in a future refactor.**

7. **Sentence chunker fix**: a single sentence longer than `chunk_size` (OCR
   noise, no terminal punctuation, one giant run-on) used to produce an
   unbounded chunk. It now flushes the buffer and falls back to fixed-window
   splitting for just that one sentence.

## What's NOT done / open questions for whoever picks this up

- **No pipeline-level integration test.** Unit tests cover
  `extract_sections`, `parse_markdown_sections`, `chunk_sections_by_heading`,
  and the XLSX/Chunker changes in isolation, but nobody has run a real DOCX
  or Markdown document through `_step2_extract_async` → `_step3_chunk_async`
  end-to-end to confirm the `doc_metadata["_docx_sections"]` handoff and
  Qdrant payload (`heading_path`, `is_table`) come out as expected.
- **No citation/UI-side check** that `heading_path` actually surfaces
  usefully in a chat citation — this branch only gets it into the Qdrant
  payload.
- **DOCX/Markdown custom heading styles**: `_TITLE_STYLES`/`_heading_level`
  in `docx_processor.py` only recognize English "Heading N"/"Title"/
  "Subtitle" styles. A localized template (e.g. French "Titre 1") silently
  degrades to the no-headings case rather than erroring — acceptable for
  now, but worth flagging if a non-English district's board docs come in.
- **Oversized DOCX tables** fall through to the sentence-aware chunker like
  any other oversized section text — i.e. a huge table gets crudely
  sentence-split rather than row-split the way XLSX does. Nobody decided
  whether that's good enough or needs its own path.
- **No decision yet on merging small adjacent same-heading chunks** — every
  short section is its own chunk today; if retrieval quality suffers from
  too many tiny chunks, the fix point is `chunk_sections_by_heading()`.
- `--tenant-id` scoping work on `scripts/school_data/feed_finalised_scrape_urls.py`
  is unrelated in-progress work on a different branch
  (`feat_Schema-crawl-results`) — currently stashed there, not part of this
  handover.

## Where to start reading

1. `app/services/document_processing/heading_chunker.py` — the shared
   packer, ~50 lines, read this first.
2. `app/services/document_processing/markdown_heading_parser.py` — same
   section shape, Markdown side.
3. `app/services/document_processing/processors/docx_processor.py` —
   `extract_sections()` at the bottom, plus `_iter_block_items` for the
   order-preserving XML walk.
4. `app/tasks/document_pipeline.py` — search `_step3_chunk_async` for the
   `elif docx_sections:` / `elif pptx_pages_text:` / `elif ctx.document_type
   in (".md", ".markdown"):` branches to see how each pre-extracted shape
   gets packed and what metadata lands on the Qdrant payload.
5. Tests: `tests/test_docx_chunking.py`, `tests/test_markdown_chunking.py`,
   `tests/test_xlsx_chunking.py`, `tests/test_chunker.py` — each documents
   the regression it guards against in its module docstring.
