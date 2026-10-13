"""
Corpus-aware retrieval fallbacks for the agentic RAG tools.

The local / historical corpus has sparse and drifting taxonomy labels
(``comprehensive`` is often empty; CHPE appears under several spellings;
coarse ``topics`` and fine ``topic_tags`` disagree). Re-classification is
not always available, so these helpers let tools fall back to:

1. Lexical ``keyword_flags`` already stored on chunks at ingest (A4/A9)
2. Semantic embedding search aggregated by district

Mappings are deliberately over-inclusive — false positives are preferable
to silent empty answers on known-present content.
"""

from __future__ import annotations

# Keyword flags stored in Qdrant payloads (canonical display forms from
# state packs / core). Only values that can actually appear in the
# payload belong here — invented strings will never match.
CATEGORY_KEYWORD_FLAGS: dict[str, tuple[str, ...]] = {
    "sexed": (
        "CHPE Framework",
        "3 Rs",
        "Get Real",
        "opt-in",
        "opt-out",
        "abstinence-only",
        "abstinence-plus",
        "sexual risk avoidance",
        "pornography",
        "indoctrination",
        "success sequencing",
    ),
    "censorship": (
        "pornography",
        "indoctrination",
    ),
    "lgbtq": (),
    "governance": (),
    "advocacy": (),
}

TOPIC_KEYWORD_FLAGS: dict[str, tuple[str, ...]] = {
    "sex_education": CATEGORY_KEYWORD_FLAGS["sexed"],
    "curriculum_censorship": CATEGORY_KEYWORD_FLAGS["censorship"],
    "parental_rights": ("opt-in", "opt-out"),
    "lgbtq_student_rights": (),
    "transgender_policy": (),
    "gender_identity": (),
    "advocacy_organizing": (),
    "school_board_election": (),
}

CATEGORY_SEMANTIC_QUERIES: dict[str, str] = {
    "sexed": (
        "comprehensive sex education CHPE Comprehensive Health and "
        "Physical Education curriculum framework sexual health"
    ),
    "censorship": (
        "book challenge curriculum censorship library materials removal"
    ),
    "lgbtq": (
        "LGBTQ transgender student policy gender identity pronouns"
    ),
    "governance": "school board governance vote policy",
    "advocacy": "advocacy organization public comment testimony petition",
}

TOPIC_SEMANTIC_QUERIES: dict[str, str] = {
    "sex_education": CATEGORY_SEMANTIC_QUERIES["sexed"],
    "curriculum_censorship": CATEGORY_SEMANTIC_QUERIES["censorship"],
    "parental_rights": (
        "parental rights policy opt-in opt-out curriculum observation"
    ),
    "lgbtq_student_rights": CATEGORY_SEMANTIC_QUERIES["lgbtq"],
    "transgender_policy": (
        "transgender student policy bathroom athletics pronouns"
    ),
    "gender_identity": "gender identity discussion board meeting",
    "advocacy_organizing": CATEGORY_SEMANTIC_QUERIES["advocacy"],
    "school_board_election": "school board election candidate",
}

ACTION_TYPE_SEMANTIC_QUERIES: dict[str, str] = {
    "book_challenged": "book challenge filed removed library materials",
    "instruction_reduced": "sex education curriculum reduced eliminated",
    "instruction_eliminated": "sex education curriculum eliminated removed",
    "protection_adopted": "student protections adopted LGBTQ policy",
    "policy_proposed": "policy proposed first reading",
    "policy_debated": "policy debated school board discussion",
}


def resolve_keyword_flags(
    *,
    keyword_flags: list[str] | None = None,
    topic_categories: list[str] | None = None,
    topics: list[str] | None = None,
) -> list[str] | None:
    """Merge explicit flags with category/topic-inferred safety-net flags."""
    resolved: list[str] = []
    seen: set[str] = set()

    def _add(values: list[str] | tuple[str, ...] | None) -> None:
        if not values:
            return
        for value in values:
            flag = (value or "").strip()
            if not flag or flag in seen:
                continue
            seen.add(flag)
            resolved.append(flag)

    _add(keyword_flags)
    for category in topic_categories or []:
        _add(CATEGORY_KEYWORD_FLAGS.get(category, ()))
    for topic in topics or []:
        _add(TOPIC_KEYWORD_FLAGS.get(topic, ()))
    return resolved or None


def resolve_semantic_query(
    *,
    semantic_query: str | None = None,
    topic_categories: list[str] | None = None,
    topics: list[str] | None = None,
    action_types: list[str] | None = None,
    topic_subtopics: list[str] | None = None,
) -> str | None:
    """Pick a semantic fallback query: explicit wins, else inferred."""
    if semantic_query and semantic_query.strip():
        return semantic_query.strip()

    parts: list[str] = []
    for category in topic_categories or []:
        q = CATEGORY_SEMANTIC_QUERIES.get(category)
        if q:
            parts.append(q)
    for topic in topics or []:
        q = TOPIC_SEMANTIC_QUERIES.get(topic)
        if q:
            parts.append(q)
    for action in action_types or []:
        q = ACTION_TYPE_SEMANTIC_QUERIES.get(action)
        if q:
            parts.append(q)
    if topic_subtopics:
        # Light lexical hint from subtopic labels themselves.
        parts.append(" ".join(s.replace("_", " ").replace(".", " ") for s in topic_subtopics))

    if not parts:
        return None
    # Deduplicate while preserving order.
    seen: set[str] = set()
    unique: list[str] = []
    for part in parts:
        if part not in seen:
            seen.add(part)
            unique.append(part)
    return " ".join(unique)
