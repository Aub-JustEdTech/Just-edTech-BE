"""Lightweight ATX-heading parser for Markdown (.md/.markdown) chunking.

Intentionally regex-based, not a full Markdown AST — the project has no
markdown/mistune/markdown-it-py dependency, and all we need is heading
boundaries (`^#{1,6}\\s`) to build a `heading_path`, the same shape
`DocxProcessor.extract_sections` produces for DOCX.

This is only ever invoked for `.md`/`.markdown` documents (gated by
extension in the pipeline, not by content) — a `.txt` file that happens to
contain `#`-prefixed lines (issue references, hashtags) must not be
misparsed as document structure, so this parser must not be reused for
`.txt`/`.text`.
"""

import re

_HEADING_RE = re.compile(r"^(#{1,6})[ \t]+(.*)$", re.MULTILINE)


def parse_markdown_sections(text: str) -> list[dict]:
    """
    Split Markdown text on ATX headings into
    `{"heading_path": str, "text": str, "is_table": False}` sections, in
    document order.

    No table detection for Markdown — pipe-tables stay as body text, same
    treatment as today.
    """
    matches = list(_HEADING_RE.finditer(text))
    if not matches:
        return []

    sections: list[dict] = []
    stack: list[tuple[int, str]] = []  # (level, heading_text)

    def _flush(body: str, heading_path: str) -> None:
        body = body.strip()
        if body:
            sections.append(
                {"heading_path": heading_path, "text": body, "is_table": False}
            )

    # Body text before the first heading, if any, has no heading_path.
    _flush(text[: matches[0].start()], "")

    for idx, match in enumerate(matches):
        level = len(match.group(1))
        heading_text = match.group(2).strip()

        while stack and stack[-1][0] >= level:
            stack.pop()
        stack.append((level, heading_text))
        heading_path = " > ".join(h for _, h in stack)

        body_start = match.end()
        body_end = matches[idx + 1].start() if idx + 1 < len(matches) else len(text)
        _flush(text[body_start:body_end], heading_path)

    return sections
