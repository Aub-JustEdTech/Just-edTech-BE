"""Stakeholder-facing report writer for district analytics.

A single LLM call turns the retrieval evidence (ranked districts +
citations) into an inverted-pyramid markdown report. The writer prompt
explicitly bans internal terms (chunks, counts, Qdrant, taxonomy, etc.)
so the PDF body reads as policy research, not a technical dump.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.llm_service import llm_service

logger = logging.getLogger(__name__)


# Terms that must never appear in the stakeholder-facing report body.
# The status / API JSON may still use technical fields; this list only
# governs the prose written into the PDF.
BANNED_TERMS: tuple[str, ...] = (
    "chunk",
    "chunks",
    "qdrant",
    "taxonomy",
    "topic_tags",
    "topic_categories",
    "topic_subtopics",
    "classifier",
    "embedding",
    "vector",
    "payload",
    "org_code",
    "chunk_count",
    "ainvoke",
    "langgraph",
    "runnableconfig",
)


REPORT_SYSTEM_PROMPT = """\
You are an education-policy research analyst writing a stakeholder report \
from retrieved school-board evidence. The audience is non-technical \
policymakers, advocates, and district staff.

You write in an INVERTED PYRAMID structure: the most important takeaway \
comes first, then expanding detail. A reader should get the headline \
answer immediately and more detail only if they keep reading.

You MUST produce the report in this exact markdown structure. Skip any \
section for which the evidence gives no detail (do not write empty \
sections). Use these headings, in this order, as markdown level-2 \
headings (##):

## Report ID
{report_id}

## Date compiled
{compiled_date}

## Documents analyzed
A 1-2 sentence stakeholder summary of the corpus (e.g. "Agendas, \
minutes, and policies from N Massachusetts school districts"). Do NOT \
mention counts of records, chunks, or vectors.

## Research goal
One sentence restating the research goal.

## Query
The exact question asked.

## Key points
The single highest-level answer to the query — one sentence or a short \
bullet list. If no districts match, say so plainly here.

## Summary
One or two paragraphs bridging the key points and the main sections.

## Documents retrieved and analyzed
Discuss the documents retrieved, the extent of coverage, and any \
coverage holes. Distinguish between districts/documents used as primary \
evidence and those only potentially relevant.

## Discussion
The substantive discussion and argument, with inline citations to \
specific meetings (e.g. "Rochester School Committee agenda, June 15, \
2026, p. 123"). When a citation includes a document_link, you may \
optionally mention it inline as a markdown link. Group by district \
where useful.

## Trend
Only include if the evidence shows a clear change over time; otherwise \
omit entirely.

## Footnotes
Only include if additional detail is needed; otherwise omit.

## References
A bibliography of every primary source used. Each entry MUST include:
  - district name
  - document name
  - meeting date
  - page number (if available)
  - a clickable markdown link to the source document when \
`document_link` is present, written as \
`[Open document](https://...)` (prefer `document_link`; if only \
`source_page_url` is present, link that as `[Source page](https://...)`)

Do NOT invent URLs. If neither link field is present, omit the link \
and list the source by name/date/page only.

LANGUAGE RULES (CRITICAL):
- Write for non-technical policy stakeholders.
- NEVER use these internal terms in the body: chunk, chunks, count, \
counts, Qdrant, taxonomy, topic_tags, topic_categories, \
topic_subtopics, classifier, embedding, vector, payload, org_code, \
chunk_count. If the evidence contains them, translate to plain \
language ("document", "district", "meeting", "agenda", "minutes", \
"policy").
- Do not report numeric "counts" of matching records. Describe \
activity qualitatively ("three districts show...", "the most active \
district is...").
- Cite real meetings using the provided citation metadata (district, \
document name, meeting date, page, links). Never invent sources or URLs.
- Keep the whole report under 5-10 pages of prose.

Return ONLY the markdown report. Do not add a top-level title heading; \
the PDF renderer adds the title.
"""


def _format_date(value: Any) -> str:
    if not value:
        return ""
    return str(value)[:10]


def _clean_snippet(text: str, limit: int = 360) -> str:
    text = (text or "").replace("\n", " ").strip()
    if len(text) > limit:
        text = text[:limit].rstrip() + "…"
    return text


def _pick_document_link(c: dict[str, Any]) -> str:
    """Prefer the downloadable source file URL, then the listing page."""
    for key in ("document_link", "source_media_url", "source_page_url"):
        val = (c.get(key) or "").strip()
        if val.startswith("http://") or val.startswith("https://"):
            return val
    return ""


def _format_citation_for_evidence(
    c: dict[str, Any], district_name: str | None = None
) -> dict[str, Any]:
    """Slim a citation dict down to what the writer needs."""
    document_link = _pick_document_link(c)
    source_page = (c.get("source_page_url") or "").strip()
    if source_page and source_page == document_link:
        source_page = ""
    return {
        "district": district_name or c.get("district_name"),
        "document": c.get("document_name"),
        "meeting_date": _format_date(c.get("meeting_date")),
        "meeting_doc_type": c.get("meeting_doc_type"),
        "page": c.get("page_number"),
        "action_stage": c.get("action_stage"),
        "snippet": _clean_snippet(c.get("snippet", "")),
        # Clickable primary-source links for the References section.
        "document_link": document_link or None,
        "source_page_url": source_page or None,
    }


def build_evidence_payload(
    spec: Any,
    corpus_summary: dict[str, Any],
    ranked: list[dict[str, Any]],
    citations: list[dict[str, Any]],
    report_id: str,
    compiled_date: str,
) -> dict[str, Any]:
    """Assemble the evidence payload sent to the writer LLM."""
    districts_used = [
        {
            "district_name": c.get("district_name"),
            "total_citations": c.get("total", len(c.get("citations", []))),
            "citations": [
                _format_citation_for_evidence(cit, district_name=c.get("district_name"))
                for cit in (c.get("citations") or [])
            ],
        }
        for c in citations
        if isinstance(c, dict) and not c.get("error")
    ]
    # A short list of districts that appeared in counts but were not drilled
    # into for citations (potentially relevant, not primary evidence).
    primary_orgs = {d.get("district_name") for d in districts_used}
    other_districts = [
        {
            "district_name": r.get("district_name"),
            "state": r.get("state"),
        }
        for r in ranked
        if r.get("district_name") not in primary_orgs
    ][:15]

    return {
        "report_id": report_id,
        "compiled_date": compiled_date,
        "title": spec.title,
        "research_goal": spec.research_goal,
        "question": spec.question,
        "geography": spec.geography,
        "corpus": {
            "district_count": corpus_summary.get("district_count", 0),
            "state": corpus_summary.get("state", "MA"),
        },
        "primary_evidence": districts_used,
        "other_matching_districts": other_districts,
    }


async def write_report(
    db: AsyncSession,
    chatbot_config_id: int,
    evidence: dict[str, Any],
) -> str:
    """Call the LLM to write the report markdown from the evidence payload."""
    user_prompt = (
        "Write the stakeholder report from the following evidence. Follow the "
        "structure and language rules in the system prompt exactly.\n\n"
        "EVIDENCE (JSON):\n"
        f"{json.dumps(evidence, ensure_ascii=False, indent=2)}\n\n"
        "REPORT:"
    )
    messages: list[dict[str, str]] = [
        {"role": "system", "content": REPORT_SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ]
    result = await llm_service.generate_chat_completion_with_config(
        db=db,
        chatbot_config_id=chatbot_config_id,
        messages=messages,
    )
    content = str(result.get("content", "")).strip()
    if not content:
        content = _fallback_report(evidence)
    return content


def _fallback_report(evidence: dict[str, Any]) -> str:
    """A deterministic, non-LLM report used if the LLM returns nothing."""
    primary = evidence.get("primary_evidence", [])
    lines: list[str] = []
    lines.append(f"## Report ID\n{evidence.get('report_id', '')}")
    lines.append(f"## Date compiled\n{evidence.get('compiled_date', '')}")
    lines.append(
        "## Documents analyzed\n"
        f"School board documents from {evidence.get('corpus', {}).get('district_count', 0)} "
        f"{evidence.get('corpus', {}).get('state', 'MA')} districts."
    )
    lines.append(f"## Research goal\n{evidence.get('research_goal', '')}")
    lines.append(f"## Query\n{evidence.get('question', '')}")
    if primary:
        names = ", ".join(d.get("district_name", "?") for d in primary)
        lines.append("## Key points")
        lines.append(f"Matching districts: {names}.")
    else:
        lines.append("## Key points")
        lines.append("No matching districts were found in the current corpus.")
    return "\n\n".join(lines)


def contains_banned_terms(text: str) -> list[str]:
    """Return the list of banned terms present in the text (case-insensitive)."""
    lower = text.lower()
    found = [term for term in BANNED_TERMS if term.lower() in lower]
    return found


def scrub_banned_terms(text: str) -> str:
    """Best-effort removal of banned terms from a report body.

    Used as a one-shot post-process when the LLM slips. The preferred path is
    a clean rewrite via the writer; this is a safety net only.
    """
    replacements = {
        "chunks": "documents",
        "chunk": "document",
        "qdrant": "knowledge base",
        "taxonomy": "topic labels",
        "topic_tags": "topics",
        "topic_categories": "topics",
        "topic_subtopics": "topics",
        "classifier": "analysis",
        "embedding": "search",
        "vector": "search",
        "payload": "metadata",
        "org_code": "district code",
        "chunk_count": "document count",
        "ainvoke": "call",
        "langgraph": "the agent",
        "runnableconfig": "config",
    }
    # Word-boundary replace, case-insensitive.
    for term, repl in replacements.items():
        pattern = re.compile(rf"\b{re.escape(term)}\b", re.IGNORECASE)
        text = pattern.sub(repl, text)
    # Soft "count"/"counts" — only strip the bare words, keep "document".
    text = re.compile(r"\bcount(s?)\b", re.IGNORECASE).sub(r"number of documents\1", text)
    return text
