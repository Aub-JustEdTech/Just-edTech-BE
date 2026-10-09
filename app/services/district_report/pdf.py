"""WeasyPrint PDF renderer for district analytics reports.

Parses the inverted-pyramid markdown structure (## headings, bullets,
paragraphs) produced by the writer and wraps it in shared Just EdTech
report branding.
"""

from __future__ import annotations

import logging
import re
from io import BytesIO

from app.services.report_branding import LINK_COLOR, wrap_branded_document

logger = logging.getLogger(__name__)


def _escape_html(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _inline_markdown(text: str) -> str:
    """Convert a subset of inline markdown to HTML (bold/italic/links)."""
    # Extract markdown links before escaping so we can re-inject <a> tags.
    link_placeholders: list[tuple[str, str, str]] = []

    def _stash_md_link(match: re.Match[str]) -> str:
        idx = len(link_placeholders)
        link_placeholders.append(("md", match.group(1), match.group(2)))
        return f"|||LINK{idx}|||"

    def _stash_bare_url(match: re.Match[str]) -> str:
        url = match.group(0)
        idx = len(link_placeholders)
        link_placeholders.append(("bare", url, url))
        return f"|||LINK{idx}|||"

    text = re.sub(r"\[([^\]]+)\]\((https?://[^)\s]+)\)", _stash_md_link, text)
    text = re.sub(r"(?<!\()(?<!\|)(https?://[^\s<>\")\]]+)", _stash_bare_url, text)

    text = _escape_html(text)
    # Bold **text**
    text = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", text)
    # Italic *text*
    text = re.sub(r"(?<!\*)\*([^*]+)\*(?!\*)", r"<em>\1</em>", text)
    # Inline code `text`
    text = re.sub(r"`([^`]+)`", r"<code>\1</code>", text)

    for idx, (kind, label, url) in enumerate(link_placeholders):
        safe_url = _escape_html(url)
        if kind == "md":
            safe_label = _escape_html(label)
        else:
            # Bare URL — show a truncated label for long links.
            safe_label = _escape_html(label if len(label) < 80 else label[:77] + "...")
        anchor = (
            f'<a href="{safe_url}" style="color:{LINK_COLOR}; text-decoration:underline;">'
            f"{safe_label}</a>"
        )
        text = text.replace(f"|||LINK{idx}|||", anchor)

    return text


def render_report_pdf(report_markdown: str, report_title: str) -> BytesIO:
    """Render the report markdown into a PDF BytesIO buffer."""
    html = _markdown_to_html(report_markdown, report_title)

    from weasyprint import HTML  # lazy import to avoid startup cost

    pdf_bytes = HTML(string=html).write_pdf()
    buf = BytesIO(pdf_bytes)
    buf.seek(0)
    return buf


def _markdown_to_html(report_markdown: str, report_title: str) -> str:
    lines = report_markdown.split("\n")
    body_parts: list[str] = [
        f"<h1>{_escape_html(report_title)}</h1>",
    ]

    in_ul = False
    in_ol = False

    def close_lists() -> None:
        nonlocal in_ul, in_ol
        if in_ul:
            body_parts.append("</ul>")
            in_ul = False
        if in_ol:
            body_parts.append("</ol>")
            in_ol = False

    for raw_line in lines:
        line = raw_line.rstrip()

        if not line.strip():
            close_lists()
            continue

        # Headings
        if line.startswith("### "):
            close_lists()
            body_parts.append(f"<h3>{_inline_markdown(line[4:].strip())}</h3>")
            continue
        if line.startswith("## "):
            close_lists()
            body_parts.append(f"<h2>{_inline_markdown(line[3:].strip())}</h2>")
            continue
        # Top-level "# title" — skip, the h1 is already rendered.
        if line.startswith("# ") and not line.startswith("## "):
            continue

        # Horizontal rule
        if line.strip() in ("---", "***"):
            close_lists()
            body_parts.append("<hr>")
            continue

        # Bullets
        if line.lstrip().startswith(("- ", "* ", "• ")):
            if not in_ul:
                close_lists()
                body_parts.append("<ul>")
                in_ul = True
            item = line.lstrip()[2:].strip()
            body_parts.append(f"<li>{_inline_markdown(item)}</li>")
            continue

        # Numbered list
        m = re.match(r"^(\d+)\.\s+(.*)$", line.lstrip())
        if m:
            if not in_ol:
                close_lists()
                body_parts.append("<ol>")
                in_ol = True
            body_parts.append(f"<li>{_inline_markdown(m.group(2))}</li>")
            continue

        # Paragraph
        close_lists()
        body_parts.append(f"<p>{_inline_markdown(line.strip())}</p>")

    close_lists()
    return wrap_branded_document("\n".join(body_parts))
