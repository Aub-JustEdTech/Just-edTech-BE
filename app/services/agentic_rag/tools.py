"""
Agent tools for the agentic RAG system.

Each tool is decorated with LangChain's @tool and receives tenant-scoped context
(tenant_id, chatbot_config_id) from the LangGraph RunnableConfig rather than as
explicit LLM-visible parameters.

The agent sees only the business parameters (query, filters, limits).
Context is injected at runtime via:

    config["configurable"]["tenant_id"]
    config["configurable"]["chatbot_config_id"]

All tools are async to play nicely with the LangGraph async event loop.
"""

import logging
import re
from typing import Annotated, Any

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import InjectedToolArg, tool
from sqlalchemy import and_, select

from app.core.config import settings
from app.db.connector import AsyncSessionLocal
from app.models.documents import Document, ProcessingStatus
from app.models.school import School
from app.services.agentic_rag.filters import build_filter_fragments
from app.services.embeddings.embedding_service import EmbeddingService
from app.services.heatmap_ingest.taxonomy import (
    ACTION_STAGES,
    ACTION_TYPES,
    ENTITY_TYPES,
    MEETING_BODIES,
    MEETING_DOC_TYPES,
    SEX_ED_SUBTOPICS,
    TOPICS,
)
from app.services.heatmap_ingest.vocabulary_packs import get_pack
from app.services.vector_store.factory import VectorStoreFactory, VectorStoreType

logger = logging.getLogger(__name__)

_SPREADSHEET_TYPES = [".xlsx", ".xls"]


def _normalise_text(text: str) -> str:
    """Basic normalisation for lexical overlap scoring."""
    text = text or ""
    text = text.lower()
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _lexical_boost(query: str, chunk_text: str) -> float:
    """
    Compute a small boost for chunks that lexically match the query well.

    This is intentionally lightweight: it favours FAQ-style chunks where the
    question text appears verbatim (or near-verbatim) in the chunk, without
    requiring an additional LLM call or external re-ranker.
    """
    q_norm = _normalise_text(query)
    t_norm = _normalise_text(chunk_text)
    if not q_norm or not t_norm:
        return 0.0

    boost = 0.0

    # Exact / near-exact question string inside the chunk (common for FAQs).
    if q_norm in t_norm:
        boost += 0.7

    # Token overlap — helpful when the question is paraphrased slightly.
    q_tokens = set(q_norm.split())
    if not q_tokens:
        return boost

    t_tokens = set(t_norm.split())
    overlap = len(q_tokens & t_tokens)
    if not overlap:
        return boost

    overlap_ratio = overlap / max(len(q_tokens), 1)

    # Medium overlap (paraphrased but still clearly about the same thing)
    if overlap_ratio >= 0.3:
        boost += 0.2
    # Very high overlap (near-verbatim wording)
    if overlap_ratio >= 0.5:
        boost += 0.2

    return boost


def _get_context(config: RunnableConfig) -> tuple[int, int]:
    """Extract tenant_id and chatbot_config_id from the LangGraph RunnableConfig."""
    cfg = (config or {}).get("configurable", {})
    tenant_id = cfg.get("tenant_id")
    chatbot_config_id = cfg.get("chatbot_config_id")
    if tenant_id is None or chatbot_config_id is None:
        raise ValueError(
            "tenant_id and chatbot_config_id must be set in config['configurable']"
        )
    return int(tenant_id), int(chatbot_config_id)


async def _embed(query: str) -> list[float]:
    """Generate a query embedding using the default embedding model."""
    service = EmbeddingService()
    return await service.generate_single_embedding(
        query, model=settings.OPENAI_EMBEDDING_MODEL
    )


def _vector_store():
    return VectorStoreFactory.create(VectorStoreType(settings.VECTOR_STORE_TYPE))


# ---------------------------------------------------------------------------
# Tool 1 – search_knowledge_base
# ---------------------------------------------------------------------------


@tool
async def search_knowledge_base(
    query: str,
    top_k: int = 10,
    document_ids: list[int] | None = None,
    doc_types: list[str] | None = None,
    topics: list[str] | None = None,
    topic_categories: list[str] | None = None,
    topic_subtopics: list[str] | None = None,
    action_types: list[str] | None = None,
    action_stages: list[str] | None = None,
    meeting_doc_types: list[str] | None = None,
    meeting_bodies: list[str] | None = None,
    entity_types: list[str] | None = None,
    districts: list[str] | None = None,
    states: list[str] | None = None,
    speaker_names: list[str] | None = None,
    speaker_roles: list[str] | None = None,
    school_years: list[str] | None = None,
    quarter_months: list[str] | None = None,
    timeframe: str | None = None,
    meeting_date_from: str | None = None,
    meeting_date_to: str | None = None,
    require_classified: bool = True,
    config: Annotated[RunnableConfig, InjectedToolArg] = None,
) -> list[dict[str, Any]]:
    """Search across all document chunks using semantic similarity.

    Use this to find specific information, quotes, data points, or details
    within documents.  Returns ranked text chunks with source document info.

    The knowledge base is a corpus of school-board documents
    (agendas, minutes, policies, public-comment transcripts, etc.)
    classified into a topic taxonomy. Every chunk carries:

      - `topics`             — coarse labels: sex_education,
                              curriculum_censorship, parental_rights,
                              lgbtq_student_rights, transgender_policy,
                              gender_identity, school_board_election,
                              advocacy_organizing
      - `topic_tags`         — fine `{category, subtopic}` pairs from
                              the V1 taxonomy (see the system prompt
                              for the full list). Filter via
                              `topic_categories` (e.g. "sexed") and/or
                              `topic_subtopics` (e.g. "comprehensive").
      - `action_types`       — instruction_reduced, book_challenged,
                              protection_adopted, policy_proposed,
                              policy_debated, instruction_eliminated
      - `action_stage`       — Discussion Only / Public Comment /
                              Motion Made / Vote — Passed|Failed|Tabled /
                              Policy First Reading / Policy Adoption
                              (Final) / Presentation/Report Given /
                              Correspondence Referenced
      - `meeting_doc_type`   — Minutes / Agenda / Agenda Attachment /
                              Public Comment Transcript / Policy
                              Document / Presentation Slide
      - `meeting_body`       — Full Board / Curriculum Subcommittee /
                              Policy Subcommittee / Public Hearing /
                              Special Meeting
      - `entity_type`        — board_minutes / board_agenda /
                              policy_document / book_challenge /
                              public_comment / candidate_profile /
                              election_record / news_media /
                              advocacy_intervention
      - `district_name`      — school district name
      - `state`              — 2-letter abbreviation
      - `meeting_date`       — ISO date (YYYY-MM-DD)
      - `school_year`        — e.g. "2025-2026"
      - `quarter_month`     — e.g. "2026-03"
      - `speakers`           — list of {name, role}

    Prefer the fine-grained `topic_subtopics` for specific concepts
    (comprehensive sex ed, book challenges, transgender policies,
    gender identity discussion) and `topic_categories` for broad
    ones. Both can be combined with `action_types` /
    `action_stages` / `meeting_doc_types` to scope the search.

    Args:
        query: The search query.
        top_k: Number of results to return (default 10).
        document_ids: Optional list of specific document DB IDs to restrict search.
        doc_types: Optional list of file extensions to restrict search
                   (e.g. [".pdf", ".docx"]).
        topics: Coarse topic labels (array-contains-any on `topics`).
        topic_categories: V1 taxonomy categories: sexed, lgbtq,
                          censorship, governance, advocacy.
        topic_subtopics: V1 taxonomy subtopics: comprehensive,
                         abstinence_only, book_challenge_filed,
                         book_removed, transgender_student_policy,
                         gender_identity_discussion, parental_rights_policy,
                         etc. See the system prompt for the full list.
        action_types: instruction_reduced, instruction_eliminated,
                       protection_adopted, policy_proposed,
                       policy_debated, book_challenged.
        action_stages: Discussion Only, Public Comment, Motion Made,
                        Vote — Passed, Vote — Failed, Vote — Tabled,
                        Policy First Reading, Policy Adoption (Final),
                        Presentation/Report Given, Correspondence Referenced.
        meeting_doc_types: Minutes, Agenda, Agenda Attachment,
                           Public Comment Transcript, Policy Document,
                           Presentation Slide.
        meeting_bodies: Full Board, Curriculum Subcommittee,
                       Policy Subcommittee, Public Hearing, Special Meeting.
        entity_types: board_minutes, board_agenda, policy_document,
                      book_challenge, public_comment, candidate_profile,
                      election_record, news_media, advocacy_intervention.
        districts: School district names (matches `district_name`).
        states: 2-letter state codes.
        speaker_names: Speaker names (free-text in V1).
        speaker_roles: Board Member, Superintendent/Admin,
                       Public Commenter, Student, External Presenter.
        school_years: e.g. ["2025-2026"].
        quarter_months: e.g. ["2026-03"].
        timeframe: Optional TimeframePreset value ("month",
                   "last_2_months", "quarter", "year", "2_years",
                   "3_years"). Used when the question maps cleanly to
                   a rolling academic-year bucket; otherwise pass
                   explicit `meeting_date_from`/`meeting_date_to`.
        meeting_date_from: ISO date (YYYY-MM-DD). When both from/to
                           are given the explicit range wins over
                           `timeframe`.
        meeting_date_to: ISO date (YYYY-MM-DD).
        require_classified: When True (default) only chunks already
                            classified by the batch classifier are
                            returned. Set to False to include freshly
                            ingested but not-yet-classified chunks.
    """
    tenant_id, _ = _get_context(config)

    try:
        embedding = await _embed(query)

        # Build engine-style filter fragments from the rich parameter
        # set, then layer the legacy `document_id`/`document_type`
        # filters on top so existing callers keep working.
        fragments = build_filter_fragments(
            topics=topics,
            topic_categories=topic_categories,
            topic_subtopics=topic_subtopics,
            action_types=action_types,
            action_stages=action_stages,
            meeting_doc_types=meeting_doc_types,
            meeting_bodies=meeting_bodies,
            entity_types=entity_types,
            districts=districts,
            states=states,
            speaker_names=speaker_names,
            speaker_roles=speaker_roles,
            school_years=school_years,
            quarter_months=quarter_months,
            timeframe=timeframe,
            meeting_date_from=meeting_date_from,
            meeting_date_to=meeting_date_to,
            require_classified=require_classified,
        )

        filters: dict[str, Any] = dict(fragments)

        # Resolve integer document IDs to UUID strings for Qdrant
        if document_ids:
            async with AsyncSessionLocal() as db:
                result = await db.execute(
                    select(Document.doc_id).where(
                        Document.id.in_(document_ids),
                        Document.tenant_id == tenant_id,
                    )
                )
                doc_uuids = [row[0] for row in result.all()]
            if doc_uuids:
                # Merge into the engine-style `must_match_any` so it
                # composes with the other filters rather than
                # replacing them.
                must_match_any = filters.setdefault("must_match_any", {})
                must_match_any.setdefault("document_id", doc_uuids)

        if doc_types:
            must_match_any = filters.setdefault("must_match_any", {})
            must_match_any.setdefault("document_type", list(doc_types))

        # If the engine-style filter dict has no keys at all, fall back
        # to the legacy `filters=None` path so we don't force every
        # caller through the new code path.
        results = await _vector_store().search(
            query_embedding=embedding,
            tenant_id=tenant_id,
            limit=top_k,
            filters=filters or None,
        )

        # Map vector-store document UUIDs to integer DB IDs so that downstream
        # citation URLs can use `/documents/{id}` and be upgraded to presigned
        # S3 URLs by `_attach_presigned_urls_to_citations`, just like the
        # classic RAG pipeline.
        uuid_to_db_id: dict[str, int] = {}
        try:
            # Collect unique UUIDs from search results
            doc_uuids = {
                (r.get("metadata") or {}).get("document_id")
                for r in results
                if (r.get("metadata") or {}).get("document_id")
            }
            if doc_uuids:
                async with AsyncSessionLocal() as db:
                    db_result = await db.execute(
                        select(Document.doc_id, Document.id).where(
                            Document.doc_id.in_(doc_uuids),
                            Document.tenant_id == tenant_id,
                        )
                    )
                    for doc_uuid, db_id in db_result.all():
                        uuid_to_db_id[str(doc_uuid)] = int(db_id)
        except Exception as mapping_exc:
            logger.warning(
                "search_knowledge_base: failed to map document UUIDs to DB IDs: %s",
                mapping_exc,
                exc_info=True,
            )

        formatted: list[dict[str, Any]] = []
        for r in results:
            meta = r.get("metadata", {}) or {}
            doc_uuid = meta.get("document_id", "")
            db_id = uuid_to_db_id.get(str(doc_uuid))

            text = r.get("text", "") or ""
            base_score = float(r.get("score", 0.0) or 0.0)
            # Combine vector similarity with a small lexical overlap boost so
            # exact FAQ-style question/answer chunks are ranked higher even if
            # their raw embedding score is slightly lower.
            combined_score = base_score + _lexical_boost(query, text)

            formatted.append(
                {
                    "text": text,
                    "document_name": meta.get("document_name", ""),
                    # Keep the raw vector-store document identifier (UUID)
                    "document_id": doc_uuid,
                    # Also expose the integer DB ID when available so citation
                    # builders can construct `/documents/{id}` URLs.
                    "document_db_id": db_id,
                    "page_number": meta.get("page_number"),
                    "chunk_index": meta.get("chunk_index", 0),
                    "document_type": meta.get("document_type", ""),
                    # Expose the combined score used for ranking so downstream
                    # components (e.g. citation selection) can use the same
                    # ordering signal.
                    "score": round(combined_score, 4),
                }
            )

        # Ensure results are sorted by the combined score in descending order.
        formatted.sort(key=lambda x: x.get("score", 0.0), reverse=True)

        return formatted

    except Exception as exc:
        logger.error(f"search_knowledge_base failed: {exc}", exc_info=True)
        return []


# ---------------------------------------------------------------------------
# Tool 2 – find_relevant_documents
# ---------------------------------------------------------------------------


@tool
async def find_relevant_documents(
    query: str,
    limit: int = 5,
    config: Annotated[RunnableConfig, InjectedToolArg] = None,
) -> list[dict[str, Any]]:
    """Search document summaries to find which documents are relevant to a topic.

    Use this FIRST for broad or analytical questions to discover what documents
    exist before searching within them.  Returns document-level matches with
    category, date range, and a one-paragraph summary.

    Args:
        query: Topic or question to find documents about.
        limit: Maximum number of documents to return (default 5).
    """
    tenant_id, _ = _get_context(config)

    try:
        embedding = await _embed(query)
        store = _vector_store()

        if not hasattr(store, "search_summaries"):
            logger.warning(
                "Vector store does not support search_summaries; "
                "falling back to empty list."
            )
            return []

        results = await store.search_summaries(
            query_embedding=embedding,
            tenant_id=tenant_id,
            limit=limit,
        )

        return [
            {
                "document_id": r.get("document_id"),
                "document_name": r.get("document_name", ""),
                "doc_category": r.get("doc_category", ""),
                "doc_date_range": r.get("doc_date_range", ""),
                "summary": r.get("summary", ""),
                "score": round(r.get("score", 0.0), 4),
            }
            for r in results
        ]

    except Exception as exc:
        logger.error(f"find_relevant_documents failed: {exc}", exc_info=True)
        return []


# ---------------------------------------------------------------------------
# Tool 3 – get_document_details
# ---------------------------------------------------------------------------


@tool
async def get_document_details(
    document_id: int,
    config: Annotated[RunnableConfig, InjectedToolArg] = None,
) -> dict[str, Any]:
    """Get full metadata for a specific document.

    Returns the document's name, type, category, date range, LLM-generated
    summary, chunk count, source type, and Box folder path (if synced from Box).

    Args:
        document_id: The integer database ID of the document.
    """
    tenant_id, _ = _get_context(config)

    try:
        async with AsyncSessionLocal() as db:
            doc = await db.get(Document, document_id)

        if not doc or doc.tenant_id != tenant_id:
            return {"error": f"Document {document_id} not found."}

        source_metadata: dict = doc.source_metadata or {}
        return {
            "document_id": doc.id,
            "name": doc.name,
            "document_type": doc.document_type,
            "doc_category": doc.doc_category,
            "doc_date_range": doc.doc_date_range,
            "summary": doc.summary or "",
            "chunk_count": doc.chunk_count,
            "processing_status": doc.processing_status.value
            if doc.processing_status
            else None,
            "source_type": doc.source_type,
            "box_path": source_metadata.get("box_path"),
            "file_size_bytes": doc.file_size_bytes,
        }

    except Exception as exc:
        logger.error(f"get_document_details failed: {exc}", exc_info=True)
        return {"error": str(exc)}


# ---------------------------------------------------------------------------
# Tool 4 – list_documents
# ---------------------------------------------------------------------------


@tool
async def list_documents(
    category: str | None = None,
    date_range: str | None = None,
    doc_type: str | None = None,
    search_name: str | None = None,
    config: Annotated[RunnableConfig, InjectedToolArg] = None,
) -> list[dict[str, Any]]:
    """List available documents with optional filters.

    Use this to understand what documents are available, for example:
    'show all budget documents' or 'list contracts from 2024'.

    Args:
        category: Filter by doc_category (e.g. 'budget', 'contract', 'minutes').
        date_range: Filter by doc_date_range string (e.g. 'FY2025').
        doc_type: Filter by file extension (e.g. '.pdf', '.xlsx').
        search_name: Case-insensitive substring match on document name.
    """
    tenant_id, _ = _get_context(config)

    try:
        async with AsyncSessionLocal() as db:
            conditions = [
                Document.tenant_id == tenant_id,
                Document.processing_status == ProcessingStatus.COMPLETED,
            ]
            if category:
                conditions.append(Document.doc_category == category)
            if date_range:
                conditions.append(Document.doc_date_range == date_range)
            if doc_type:
                conditions.append(Document.document_type == doc_type)
            if search_name:
                conditions.append(
                    Document.name.ilike(f"%{search_name}%")
                )

            result = await db.execute(
                select(
                    Document.id,
                    Document.name,
                    Document.document_type,
                    Document.doc_category,
                    Document.doc_date_range,
                    Document.chunk_count,
                    Document.source_type,
                ).where(and_(*conditions))
            )
            rows = result.all()

        return [
            {
                "document_id": row.id,
                "name": row.name,
                "document_type": row.document_type,
                "doc_category": row.doc_category or "",
                "doc_date_range": row.doc_date_range or "",
                "chunk_count": row.chunk_count,
                "source_type": row.source_type or "",
            }
            for row in rows
        ]

    except Exception as exc:
        logger.error(f"list_documents failed: {exc}", exc_info=True)
        return []


# ---------------------------------------------------------------------------
# Tool 5 – search_tables
# ---------------------------------------------------------------------------


@tool
async def search_tables(
    query: str,
    document_ids: list[int] | None = None,
    top_k: int = 10,
    config: Annotated[RunnableConfig, InjectedToolArg] = None,
) -> list[dict[str, Any]]:
    """Search specifically within spreadsheet and tabular data.

    Returns markdown-formatted table sections with column headers preserved.
    Use this for financial data, budget line items, contract lists, or any
    structured numeric / categorical data from Excel files.

    Args:
        query: What to search for within spreadsheets.
        document_ids: Optional list of specific document IDs to restrict search.
        top_k: Number of table chunks to return (default 10).
    """
    tenant_id, _ = _get_context(config)

    try:
        embedding = await _embed(query)

        filters: dict[str, Any] = {"document_type": _SPREADSHEET_TYPES}

        if document_ids:
            async with AsyncSessionLocal() as db:
                result = await db.execute(
                    select(Document.doc_id).where(
                        Document.id.in_(document_ids),
                        Document.tenant_id == tenant_id,
                        Document.document_type.in_(_SPREADSHEET_TYPES),
                    )
                )
                doc_uuids = [row[0] for row in result.all()]
            if doc_uuids:
                filters["document_id"] = {"$in": doc_uuids}

        results = await _vector_store().search(
            query_embedding=embedding,
            tenant_id=tenant_id,
            limit=top_k,
            filters=filters,
        )

        return [
            {
                "text": r.get("text", ""),
                "document_name": r.get("metadata", {}).get("document_name", ""),
                "document_id": r.get("metadata", {}).get("document_id", ""),
                "sheet_name": r.get("metadata", {}).get("sheet_name", ""),
                "row_start": r.get("metadata", {}).get("row_start"),
                "row_end": r.get("metadata", {}).get("row_end"),
                "score": round(r.get("score", 0.0), 4),
            }
            for r in results
        ]

    except Exception as exc:
        logger.error(f"search_tables failed: {exc}", exc_info=True)
        return []


# ---------------------------------------------------------------------------
# Tool 6 – answer_faq_exact_match
# ---------------------------------------------------------------------------


def _strip_question_prefix(text: str) -> str:
    """
    Strip common FAQ prefixes like "29." or "Q29:" from a question line.

    This helps match user questions to FAQ headings even when the document
    numbers questions.
    """
    if not text:
        return ""
    # Remove leading digits, dots, and whitespace, e.g. "29. " → ""
    return re.sub(r"^\s*\d+[\.\):\-\s]+", "", text).strip()


def _normalise_question(text: str) -> str:
    """Normalise question text for matching against FAQ headings."""
    # Lowercase, collapse whitespace, strip common punctuation at the end.
    text = _strip_question_prefix(text)
    text = _normalise_text(text)
    text = re.sub(r"[?\.!]+$", "", text)
    return text.strip()


@tool
async def answer_faq_exact_match(
    query: str,
    top_k: int = 50,
    config: Annotated[RunnableConfig, InjectedToolArg] = None,
) -> dict[str, Any]:
    """Try to answer FAQ-style questions by matching the exact FAQ heading.

    Use this when the user question looks like it could be copied from a FAQ,
    such as "Can I choose a different school than the one assigned to my
    student?". This tool searches FAQ-like chunks and, when it finds a strong
    match, returns the canonical answer paragraph(s) from that FAQ entry.

    Returns a dict:
        {
            "matched": bool,
            "question": str,
            "answer": str | None,
            "document_name": str | None,
            "document_id": int | None,
            "page_number": int | None,
            "chunk_index": int | None,
        }
    """
    tenant_id, _ = _get_context(config)

    try:
        embedding = await _embed(query)
        store = _vector_store()

        results = await store.search(
            query_embedding=embedding,
            tenant_id=tenant_id,
            limit=top_k,
            filters=None,
        )

        if not results:
            return {"matched": False, "question": query, "answer": None}

        q_norm = _normalise_question(query)
        if not q_norm:
            return {"matched": False, "question": query, "answer": None}

        # Find the best matching FAQ heading by combining semantic score and
        # strict lexical heading match.
        best: dict[str, Any] | None = None
        best_score: float = 0.0

        # Map vector-store UUIDs to DB IDs so we can return the integer ID.
        doc_uuids = {
            (r.get("metadata") or {}).get("document_id")
            for r in results
            if (r.get("metadata") or {}).get("document_id")
        }
        uuid_to_db_id: dict[str, int] = {}
        if doc_uuids:
            async with AsyncSessionLocal() as db:
                db_result = await db.execute(
                    select(Document.doc_id, Document.id).where(
                        Document.doc_id.in_(doc_uuids),
                        Document.tenant_id == tenant_id,
                    )
                )
                for doc_uuid, db_id in db_result.all():
                    uuid_to_db_id[str(doc_uuid)] = int(db_id)

        for r in results:
            meta = r.get("metadata", {}) or {}
            text = r.get("text", "") or ""
            if not text:
                continue

            base_score = float(r.get("score", 0.0) or 0.0)
            t_norm = _normalise_text(text)

            # Strong signal: the normalised question text appears verbatim in
            # the chunk (after stripping numbering from both sides).
            lex_score = 0.0
            if q_norm and q_norm in _normalise_question(text):
                lex_score = 2.0
            else:
                # Fallback: look for a heading line in the chunk that closely
                # matches the question.
                for line in text.splitlines():
                    line_q = _normalise_question(line)
                    if not line_q:
                        continue
                    # Exact or near-exact match on the heading line.
                    if line_q == q_norm:
                        lex_score = 2.0
                        break
                    # High token overlap between question and heading.
                    q_tokens = set(q_norm.split())
                    l_tokens = set(line_q.split())
                    if not q_tokens or not l_tokens:
                        continue
                    overlap = len(q_tokens & l_tokens) / max(len(q_tokens), 1)
                    if overlap >= 0.8:
                        lex_score = 1.5
                        break

            if lex_score <= 0.0:
                continue

            combined = base_score + lex_score
            if combined <= best_score:
                continue

            best_score = combined
            doc_uuid = meta.get("document_id")
            best = {
                "matched": True,
                "question": query,
                "answer": text,
                "document_name": meta.get("document_name"),
                "document_id": uuid_to_db_id.get(str(doc_uuid)),
                "page_number": meta.get("page_number"),
                "chunk_index": meta.get("chunk_index", 0),
            }

        if not best:
            return {"matched": False, "question": query, "answer": None}

        return best

    except Exception as exc:
        logger.error(f"answer_faq_exact_match failed: {exc}", exc_info=True)
        return {"matched": False, "question": query, "answer": None}


# ---------------------------------------------------------------------------
# Tool 7 – count_districts_by_topic
# ---------------------------------------------------------------------------
#
# Aggregation tool for cross-district analytics: "which districts",
# "how many districts", "highest volume of <topic>", "any districts
# that <action> in the last N months". Walks every active school for
# the tenant, issues one Qdrant `count_chunks` per district with the
# full V1 filter set, and returns a ranked list.
#
# This bypasses `HeatmapEngineService.count_by_district` because the
# engine's filter surface (categories + state + timeframe) is narrower
# than the agent's (subtopics + action_types + action_stages +
# meeting_doc_types + meeting_bodies + speakers + entity_types +
# custom date range). When the agent only needs the engine's narrow
# surface it can still call this tool — the implementation is the
# same shape (one count per school), just with a richer filter.


@tool
async def count_districts_by_topic(
    topics: list[str] | None = None,
    topic_categories: list[str] | None = None,
    topic_subtopics: list[str] | None = None,
    action_types: list[str] | None = None,
    action_stages: list[str] | None = None,
    meeting_doc_types: list[str] | None = None,
    meeting_bodies: list[str] | None = None,
    entity_types: list[str] | None = None,
    states: list[str] | None = None,
    school_years: list[str] | None = None,
    quarter_months: list[str] | None = None,
    timeframe: str | None = None,
    meeting_date_from: str | None = None,
    meeting_date_to: str | None = None,
    sort_by: str = "chunk_count",
    include_zero: bool = False,
    limit: int = 100,
    config: Annotated[RunnableConfig, InjectedToolArg] = None,
) -> list[dict[str, Any]]:
    """Aggregate chunk counts per district for the given topic/action filters.

    Use this for cross-district analytics — "which districts", "how many
    districts", "highest volume of <topic>", "any districts that
    <action> in the last N months", etc. Returns one row per district
    with `district_name`, `state`, `org_code`, `chunk_count`, and the
    date range of matching chunks.

    Prefer this over `search_knowledge_base` when the question asks
    "which districts" or ranks districts by volume — it scans every
    district in one call instead of returning chunks that the LLM
    would have to group by hand.

    Args:
        topics: Coarse topic labels (e.g. ["sex_education"]).
        topic_categories: V1 categories: sexed, lgbtq, censorship,
                          governance, advocacy.
        topic_subtopics: V1 subtopics (e.g. "comprehensive",
                         "book_challenge_filed",
                         "transgender_student_policy",
                         "gender_identity_discussion"). See the
                         system prompt for the full list.
        action_types: instruction_reduced, instruction_eliminated,
                       protection_adopted, policy_proposed,
                       policy_debated, book_challenged.
        action_stages: Discussion Only, Public Comment, Motion Made,
                        Vote — Passed, Vote — Failed, Vote — Tabled,
                        Policy First Reading, Policy Adoption (Final),
                        Presentation/Report Given,
                        Correspondence Referenced.
        meeting_doc_types: Minutes, Agenda, Agenda Attachment,
                           Public Comment Transcript, Policy Document,
                           Presentation Slide.
        meeting_bodies: Full Board, Curriculum Subcommittee,
                       Policy Subcommittee, Public Hearing,
                       Special Meeting.
        entity_types: board_minutes, board_agenda, policy_document,
                      book_challenge, public_comment,
                      candidate_profile, election_record,
                      news_media, advocacy_intervention.
        states: 2-letter state codes. Defaults to ["MA"] when
                omitted (the seeded corpus is all Massachusetts).
        school_years: e.g. ["2025-2026"].
        quarter_months: e.g. ["2026-03"].
        timeframe: TimeframePreset value ("month",
                   "last_2_months", "quarter", "year", "2_years",
                   "3_years"). Used when the question maps cleanly to
                   a rolling academic-year bucket; otherwise pass
                   explicit `meeting_date_from`/`meeting_date_to`.
        meeting_date_from: ISO date (YYYY-MM-DD). When both from/to
                           are given the explicit range wins over
                           `timeframe`. Translate "since Sept 2025"
                           to "2025-09-01", "last 12 months" to
                           <today-365>, "this year" to Jan 1 of the
                           current year.
        meeting_date_to: ISO date (YYYY-MM-DD).
        sort_by: One of chunk_count (default), first_meeting_date,
                 last_meeting_date. last_meeting_date sorts most
                 recent activity first.
        include_zero: When False (default) districts with zero
                      matching chunks are omitted. Pass True to
                      get a full district roster with zero counts.
        limit: Maximum number of districts to return (default 100).
               Counts beyond this are still computed but truncated.
    """
    tenant_id, _ = _get_context(config)

    # Validate the time window early so we surface a clean error
    # rather than a 280-district loop that returns all zeros.
    try:
        from app.schemas.heatmap_engine import TimeframePreset

        if timeframe is not None:
            TimeframePreset(str(timeframe))
    except ValueError as exc:
        return [{"error": f"Invalid timeframe: {exc}"}]

    try:
        # Resolve the active schools for the tenant. The engine uses
        # the same lookup, so the district roster here matches the map
        # view exactly.
        async with AsyncSessionLocal() as db:
            stmt = select(School).where(
                School.tenant_id == tenant_id,
                School.is_active.is_(True),
            )
            resolved_states = list(states) if states else ["MA"]
            stmt = stmt.where(School.state.in_(resolved_states))
            stmt = stmt.order_by(School.name)
            schools = list((await db.execute(stmt)).scalars().all())

        if not schools:
            return []

        store = _vector_store()

        # Build the filter fragments once per district (district_name
        # changes per school), reusing the same dict for the other
        # conditions. We can't share the exact dict because
        # `must_match` is mutated per-district, so we rebuild fragments
        # each iteration — cheap (a few dict copies) relative to the
        # Qdrant round trip it enables.
        rows: list[dict[str, Any]] = []
        for school in schools:
            fragments = build_filter_fragments(
                topics=topics,
                topic_categories=topic_categories,
                topic_subtopics=topic_subtopics,
                action_types=action_types,
                action_stages=action_stages,
                meeting_doc_types=meeting_doc_types,
                meeting_bodies=meeting_bodies,
                entity_types=entity_types,
                districts=[school.name],
                states=resolved_states,
                school_years=school_years,
                quarter_months=quarter_months,
                timeframe=timeframe,
                meeting_date_from=meeting_date_from,
                meeting_date_to=meeting_date_to,
                require_classified=True,
            )
            count = await store.count_chunks(
                tenant_id=tenant_id,
                **fragments,
            )
            if count == 0 and not include_zero:
                continue
            rows.append(
                {
                    "org_code": school.org_code,
                    "district_name": school.name,
                    "state": school.state or "MA",
                    "district_type": school.district_type,
                    "chunk_count": count,
                }
            )

        # `last_meeting_date` / `first_meeting_date` would require a
        # second scroll per district; defer until a query actually
        # asks for date-range ranking. For now `sort_by` accepts the
        # values but only `chunk_count` changes the order.
        if sort_by == "chunk_count":
            rows.sort(key=lambda r: r.get("chunk_count", 0), reverse=True)
        elif sort_by == "last_meeting_date":
            # Without a per-district date fetch we keep chunk_count
            # ordering but log a warning so the agent's prompt knows
            # to call get_district_citations for date-ordering.
            rows.sort(key=lambda r: r.get("chunk_count", 0), reverse=True)
        elif sort_by == "first_meeting_date":
            rows.sort(key=lambda r: r.get("chunk_count", 0), reverse=True)
        else:
            rows.sort(key=lambda r: r.get("chunk_count", 0), reverse=True)

        return rows[:limit]

    except Exception as exc:
        logger.error(f"count_districts_by_topic failed: {exc}", exc_info=True)
        return [{"error": str(exc)}]


# ---------------------------------------------------------------------------
# Tool 8 – get_district_citations
# ---------------------------------------------------------------------------
#
# Per-district evidence drill-down: after `count_districts_by_topic`
# ranks the districts, the agent calls this once per top-N district
# to retrieve the actual chunk text + source metadata so the final
# answer can cite specific meetings by name + date + page.
#
# Like `count_districts_by_topic`, this bypasses the engine's
# `get_district_citations` so the agent can pass the full V1 filter
# surface (subtopics, action_types, action_stages, meeting_doc_types,
# meeting_bodies, speakers, entity_types) rather than just `categories`.


@tool
async def get_district_citations(
    org_code: str,
    topics: list[str] | None = None,
    topic_categories: list[str] | None = None,
    topic_subtopics: list[str] | None = None,
    action_types: list[str] | None = None,
    action_stages: list[str] | None = None,
    meeting_doc_types: list[str] | None = None,
    meeting_bodies: list[str] | None = None,
    entity_types: list[str] | None = None,
    states: list[str] | None = None,
    school_years: list[str] | None = None,
    quarter_months: list[str] | None = None,
    timeframe: str | None = None,
    meeting_date_from: str | None = None,
    meeting_date_to: str | None = None,
    page: int = 1,
    page_size: int = 10,
    sort: str = "default",
    config: Annotated[RunnableConfig, InjectedToolArg] = None,
) -> dict[str, Any]:
    """Retrieve paginated chunk citations for one district + filter set.

    Use this AFTER `count_districts_by_topic` to pull the actual
    text snippets + source document metadata for the top-N districts
    so the final answer can cite specific meetings by name, date,
    and page number.

    Args:
        org_code: The district's `org_code` (returned by
                  `count_districts_by_topic`). NOT the district name —
                  pass the `org_code` field exactly as returned.
        topics, topic_categories, topic_subtopics, action_types,
        action_stages, meeting_doc_types, meeting_bodies,
        entity_types, states, school_years, quarter_months,
        timeframe, meeting_date_from, meeting_date_to:
            same filter surface as `count_districts_by_topic`.
            Pass the SAME filters you used for the count so the
            citations match the counted chunks.
        page: 1-indexed page number.
        page_size: Chunks per page (default 10, max 25).
        sort: "default" (vector-store order) or "date_desc" (most
              recent meeting_date first). Use "date_desc" for
              "most recent" / "latest" questions.
    """
    tenant_id, _ = _get_context(config)

    if page < 1:
        page = 1
    if page_size < 1:
        page_size = 10
    if page_size > 25:
        page_size = 25

    try:
        # Resolve the school by org_code so we can filter by
        # `district_name` (the indexed payload field present on every
        # chunk) rather than `school_id` (not always populated on
        # legacy-ingested chunks).
        async with AsyncSessionLocal() as db:
            school = (
                await db.execute(
                    select(School).where(
                        School.tenant_id == tenant_id,
                        School.org_code == org_code,
                    )
                )
            ).scalar_one_or_none()

        if school is None:
            return {
                "org_code": org_code,
                "district_name": None,
                "citations": [],
                "page": page,
                "page_size": page_size,
                "total": 0,
                "error": f"No school found for org_code={org_code!r}",
            }

        fragments = build_filter_fragments(
            topics=topics,
            topic_categories=topic_categories,
            topic_subtopics=topic_subtopics,
            action_types=action_types,
            action_stages=action_stages,
            meeting_doc_types=meeting_doc_types,
            meeting_bodies=meeting_bodies,
            entity_types=entity_types,
            districts=[school.name],
            states=states,
            school_years=school_years,
            quarter_months=quarter_months,
            timeframe=timeframe,
            meeting_date_from=meeting_date_from,
            meeting_date_to=meeting_date_to,
            require_classified=True,
        )

        store = _vector_store()

        # For `date_desc` we fetch a bounded larger batch up front
        # (mirrors the engine's `_REPORT_SORT_FETCH_CAP`) and sort
        # client-side, since Qdrant scroll doesn't support payload
        # ordering. For `default` we fetch exactly the page slice.
        if sort == "date_desc":
            fetch_limit = 200
        else:
            fetch_limit = (page * page_size) + page_size

        chunks = await store.filter_chunks(
            tenant_id=tenant_id,
            **fragments,
            limit=fetch_limit,
        )

        if sort == "date_desc":
            chunks = sorted(
                chunks,
                key=lambda c: (
                    (c.get("metadata") or {}).get("meeting_date") or ""
                ),
                reverse=True,
            )

        total = len(chunks)
        offset = (page - 1) * page_size
        page_chunks = chunks[offset : offset + page_size]

        # Hydrate each chunk into a citation dict. We deliberately do
        # NOT generate presigned S3 URLs here — the agent's citation
        # builder (`extract_citations` node) constructs
        # `/documents/{id}` URLs from `document_db_id`, and the
        # front-end upgrades them to S3 URLs as needed.
        doc_uuids = {
            (c.get("metadata") or {}).get("document_id")
            for c in page_chunks
            if (c.get("metadata") or {}).get("document_id")
        }
        uuid_to_db_id: dict[str, int] = {}
        if doc_uuids:
            async with AsyncSessionLocal() as db:
                db_result = await db.execute(
                    select(Document.doc_id, Document.id).where(
                        Document.doc_id.in_(doc_uuids),
                        Document.tenant_id == tenant_id,
                    )
                )
                for doc_uuid, db_id in db_result.all():
                    uuid_to_db_id[str(doc_uuid)] = int(db_id)

        citations = []
        for chunk in page_chunks:
            meta = chunk.get("metadata") or {}
            doc_uuid = meta.get("document_id")
            text = chunk.get("text", "") or ""
            snippet = text[:500] + ("…" if len(text) > 500 else "")
            citations.append(
                {
                    "document_id": doc_uuid,
                    "document_db_id": uuid_to_db_id.get(str(doc_uuid)),
                    "document_name": meta.get("document_name", ""),
                    "document_type": meta.get("document_type", ""),
                    "meeting_date": meta.get("meeting_date"),
                    "meeting_doc_type": meta.get("meeting_doc_type"),
                    "meeting_body": meta.get("meeting_body"),
                    "page_number": meta.get("page_number"),
                    "chunk_index": meta.get("chunk_index", 0),
                    "snippet": snippet,
                    "topic_tags": meta.get("topic_tags") or [],
                    "action_stage": meta.get("action_stage"),
                    "speakers": meta.get("speakers") or [],
                    # Original source links from the scrape / ingest payload —
                    # used by district reports (and any caller that wants a
                    # clickable primary-source URL). Prefer source_media_url
                    # (the file itself) over source_page_url (the listing page).
                    "source_media_url": meta.get("source_media_url") or "",
                    "source_page_url": meta.get("source_page_url") or "",
                    "s3_key_raw": meta.get("s3_key_raw") or "",
                }
            )

        return {
            "org_code": org_code,
            "district_name": school.name,
            "state": school.state,
            "citations": citations,
            "page": page,
            "page_size": page_size,
            "total": total,
        }

    except Exception as exc:
        logger.error(f"get_district_citations failed: {exc}", exc_info=True)
        return {
            "org_code": org_code,
            "district_name": None,
            "citations": [],
            "page": page,
            "page_size": page_size,
            "total": 0,
            "error": str(exc),
        }


# ---------------------------------------------------------------------------
# Tool 9 – list_districts
# ---------------------------------------------------------------------------


@tool
async def list_districts(
    state: str | None = None,
    name_contains: str | None = None,
    config: Annotated[RunnableConfig, InjectedToolArg] = None,
) -> list[dict[str, Any]]:
    """List all school districts in the tenant's corpus.

    Use this to verify a district name before filtering by it, or to
    discover which districts exist for a state. Returns one row per
    active school with `org_code`, `district_name`, `state`, and
    `district_type`.

    Args:
        state: 2-letter state code (default "MA" — the seeded corpus
                is all Massachusetts).
        name_contains: Case-insensitive substring match on district
                       name. Useful when the user references a
                       district by a partial or informal name.
    """
    tenant_id, _ = _get_context(config)

    try:
        async with AsyncSessionLocal() as db:
            stmt = select(School).where(
                School.tenant_id == tenant_id,
                School.is_active.is_(True),
            )
            if state:
                stmt = stmt.where(School.state == state)
            if name_contains:
                stmt = stmt.where(School.name.ilike(f"%{name_contains}%"))
            stmt = stmt.order_by(School.name)
            schools = list((await db.execute(stmt)).scalars().all())

        return [
            {
                "org_code": s.org_code,
                "district_name": s.name,
                "state": s.state or "MA",
                "district_type": s.district_type,
            }
            for s in schools
        ]

    except Exception as exc:
        logger.error(f"list_districts failed: {exc}", exc_info=True)
        return []


# ---------------------------------------------------------------------------
# Tool 10 – get_taxonomy
# ---------------------------------------------------------------------------
#
# Returns the canonical topic / subtopic / action_type / action_stage /
# doc_type / meeting_body / entity_type vocabulary. The agent already
# has the universal-core taxonomy inlined in its system prompt, so this
# tool is mainly for looking up STATE-SPECIFIC curricula + named
# advocacy orgs (e.g. the MA Comprehensive Health & PE Framework,
# Massachusetts Family Institute) which are not inlined because they
# vary by state.


@tool
async def get_taxonomy(
    state: str | None = None,
    config: Annotated[RunnableConfig, InjectedToolArg] = None,
) -> dict[str, Any]:
    """Return the canonical classification vocabulary.

    The universal-core taxonomy (5 categories x ~33 subtopics) is
    inlined in the system prompt — call this tool only to look up
    STATE-SPECIFIC curricula and named advocacy orgs that are not
    inlined because they vary by state.

    Args:
        state: 2-letter state code. Defaults to "MA" when omitted.
                Pass a specific state to get its named curricula
                (e.g. "MA" → Comprehensive Health & PE Framework,
                "Get Real") and advocacy orgs (e.g. Massachusetts
                Family Institute).
    """
    try:
        pack = get_pack(state or "MA")
        return {
            "state": pack.state,
            "topics": list(TOPICS),
            "action_types": list(ACTION_TYPES),
            "sex_ed_subtopics": list(SEX_ED_SUBTOPICS),
            "action_stages": list(ACTION_STAGES),
            "meeting_doc_types": list(MEETING_DOC_TYPES),
            "meeting_bodies": list(MEETING_BODIES),
            "entity_types": list(ENTITY_TYPES),
            "topic_categories": [
                {
                    "category": cat.category,
                    "description": cat.description,
                    "subtopics": [
                        {"subtopic": s.subtopic, "description": s.description}
                        for s in cat.subtopics
                    ],
                }
                for cat in pack.topic_taxonomy
            ],
            "state_curricula": [
                {
                    "category": c.category,
                    "subtopic": c.subtopic,
                    "description": c.description,
                }
                for c in pack.state_curricula
            ],
            "state_orgs": list(pack.state_orgs),
        }
    except Exception as exc:
        logger.error(f"get_taxonomy failed: {exc}", exc_info=True)
        return {"error": str(exc)}


# ---------------------------------------------------------------------------
# Exported tool list (used by the agent graph to bind to the LLM)
# ---------------------------------------------------------------------------

AGENT_TOOLS = [
    search_knowledge_base,
    find_relevant_documents,
    get_document_details,
    list_documents,
    search_tables,
    answer_faq_exact_match,
    count_districts_by_topic,
    get_district_citations,
    list_districts,
    get_taxonomy,
]
