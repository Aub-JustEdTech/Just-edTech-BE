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
from app.services.embeddings.embedding_service import EmbeddingService
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
    config: Annotated[RunnableConfig, InjectedToolArg] = None,
) -> list[dict[str, Any]]:
    """Search across all document chunks using semantic similarity.

    Use this to find specific information, quotes, data points, or details
    within documents.  Returns ranked text chunks with source document info.

    Args:
        query: The search query.
        top_k: Number of results to return (default 10).
        document_ids: Optional list of specific document DB IDs to restrict search.
        doc_types: Optional list of file extensions to restrict search
                   (e.g. [".pdf", ".docx"]).
    """
    tenant_id, _ = _get_context(config)

    try:
        embedding = await _embed(query)

        filters: dict[str, Any] = {}

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
                filters["document_id"] = {"$in": doc_uuids}

        if doc_types:
            filters["document_type"] = doc_types

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
# Exported tool list (used by the agent graph to bind to the LLM)
# ---------------------------------------------------------------------------

AGENT_TOOLS = [
    search_knowledge_base,
    find_relevant_documents,
    get_document_details,
    list_documents,
    search_tables,
    answer_faq_exact_match,
]
