"""
Keyword recall flag matching, per spec A4.

Lexical, deliberately over-inclusive matching against a state pack's
keyword list. Computed deterministically at ingest (step 5) and stored in
the Qdrant payload as `keyword_flags: [str]`, independent of the LLM
classifier. Reconciliation against classified tags happens during
spot-check / eval, not at ingest.
"""

from __future__ import annotations

import re
from typing import NamedTuple


class KeywordFlagSpec(NamedTuple):
    """A compiled keyword flag matcher.

    `display` is the canonical form stored in the payload (e.g. "3 Rs").
    `pattern` matches case-insensitively, with word boundaries adjusted so
    multi-word phrases and phrases containing digits match correctly.
    """

    display: str
    pattern: re.Pattern[str]


def _compile(display: str) -> KeywordFlagSpec:
    # Use a lookbehind before and a lookahead after so phrases like "3 Rs"
    # and "opt-in" match between punctuation/spaces but not inside larger
    # words. Treat any non-alphanumeric character as a boundary.
    before = r"(?<![A-Za-z0-9])"
    after = r"(?![A-Za-z0-9])"
    escaped = re.escape(display)
    pattern = re.compile(before + escaped + after, re.IGNORECASE)
    return KeywordFlagSpec(display=display, pattern=pattern)


def match_keyword_flags(
    text: str, keyword_flags: tuple[str, ...]
) -> list[str]:
    """Return the canonical display forms of all keyword flags present in `text`.

    Deduplicated, preserving the order of `keyword_flags`. Empty text returns
    an empty list.
    """
    if not text or not keyword_flags:
        return []
    seen: set[str] = set()
    out: list[str] = []
    for flag in keyword_flags:
        if flag in seen:
            continue
        spec = _compile(flag)
        if spec.pattern.search(text):
            out.append(flag)
            seen.add(flag)
    return out
