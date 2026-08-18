"""Tests for the Markdown ATX-heading parser used to chunk .md/.markdown
documents. Deliberately gated on file extension, not content — see the
module docstring in markdown_heading_parser.py for why a .txt file with
'#'-prefixed lines must never be run through this parser.
"""

from __future__ import annotations

from app.services.document_processing.markdown_heading_parser import (
    parse_markdown_sections,
)


def test_nested_headings_produce_correct_heading_path():
    text = (
        "# Board Policy\n"
        "Intro text.\n\n"
        "## Section 3.2\n"
        "Nested body text.\n\n"
        "### 3.2.1 Detail\n"
        "Deepest body text.\n"
    )
    sections = parse_markdown_sections(text)
    paths = [s["heading_path"] for s in sections]

    assert "Board Policy" in paths
    assert "Board Policy > Section 3.2" in paths
    assert "Board Policy > Section 3.2 > 3.2.1 Detail" in paths


def test_sibling_headings_do_not_nest():
    text = "# One\nBody one.\n\n# Two\nBody two.\n"
    sections = parse_markdown_sections(text)
    paths = [s["heading_path"] for s in sections]
    assert paths == ["One", "Two"]


def test_body_before_first_heading_has_empty_heading_path():
    text = "Preamble text.\n\n# First Heading\nBody.\n"
    sections = parse_markdown_sections(text)
    assert sections[0]["heading_path"] == ""
    assert "Preamble text." in sections[0]["text"]


def test_heading_with_no_body_is_not_emitted_as_empty_section():
    text = "# Parent\n## Child\nOnly child has body text.\n"
    sections = parse_markdown_sections(text)
    assert len(sections) == 1
    assert sections[0]["heading_path"] == "Parent > Child"


def test_no_headings_returns_empty_list():
    """Callers fall through to sentence-strategy chunking on an empty list —
    this parser must not fabricate a heading_path when none exists."""
    text = "Just plain text with no ATX headings at all."
    assert parse_markdown_sections(text) == []


def test_hash_lines_that_are_not_atx_headings_need_a_space():
    """'#tag' (no space) is not valid ATX syntax and must not be treated as
    a heading — this is what keeps a .txt hashtag/issue-ref line safe if
    this parser were ever misapplied to one."""
    text = "#nospace is not a heading\n\nBut this is body text."
    assert parse_markdown_sections(text) == []
