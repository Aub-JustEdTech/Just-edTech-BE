"""
Document summarizer service.

Calls the LLM with the first 6 000 characters of a document's extracted text
to produce a structured summary (type, date range, key topics, key entities,
paragraph summary).  The result is:
  - Persisted to the `documents` table (summary, doc_category, doc_date_range).
  - Embedded and stored in the Qdrant `tenant_{id}_summaries` collection so the
    `find_relevant_documents` agent tool can search at the document level.
"""

import json
import logging
from typing import Any

from openai import AsyncOpenAI
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.documents import Document
from app.services.embeddings.embedding_service import EmbeddingService

logger = logging.getLogger(__name__)

# Maximum characters of extracted text fed to the summariser.
_SUMMARY_TEXT_LIMIT = 6_000

_SYSTEM_PROMPT = """\
You are a document analysis assistant.  You will be given a portion of text
extracted from a document and must return a JSON object with the following keys:

  "doc_type"     – The document type as a short label.  Use one of:
                   budget, minutes, contract, report, policy, facilities,
                   curriculum, hr, legal, correspondence, other.
  "date_range"   – The time period the document covers, e.g. "FY2025",
                   "2024-03", "2022–2024", "unknown".
  "key_topics"   – A JSON array of 3–5 short bullet-point strings.
  "key_entities" – A JSON array of notable named entities: schools, programs,
                   staff roles, dollar amounts, dates.
  "summary"      – A single paragraph (3–5 sentences) summarising the document.

Return ONLY the JSON object.  Do not wrap it in markdown fences.
"""

_USER_TEMPLATE = """\
Document text (first {char_count} characters):

{text}
"""


class DocumentSummarizer:
    """
    Generates and persists a structured summary for a single document.

    Usage
    -----
    summarizer = DocumentSummarizer()
    result = await summarizer.summarize(
        text=ctx.extracted_text,
        document_id=ctx.document_id,
        doc_uuid=ctx.doc_uuid,
        document_name=document.name,
        tenant_id=ctx.tenant_id,
        db=db,
    )
    """

    def __init__(self, model: str = "gpt-4o-mini"):
        self._model = model
        self._client = (
            AsyncOpenAI(api_key=settings.OPENAI_API_KEY)
            if settings.OPENAI_API_KEY
            else None
        )
        self._embedding_service = EmbeddingService()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def summarize(
        self,
        text: str,
        document_id: int,
        doc_uuid: str,
        document_name: str,
        tenant_id: int,
        db: AsyncSession,
    ) -> dict[str, Any]:
        """
        Generate a summary, persist it to PostgreSQL, and index the embedding
        in Qdrant.

        Returns the parsed summary dict (doc_type, date_range, key_topics,
        key_entities, summary), or an empty dict if summarisation is skipped
        due to missing API key.
        """
        if not self._client:
            logger.warning(
                f"[Doc {document_id}] OpenAI API key not configured – skipping summarisation."
            )
            return {}

        truncated = text[:_SUMMARY_TEXT_LIMIT]
        parsed = await self._call_llm(truncated, document_id)
        if not parsed:
            return {}

        await self._persist_to_db(parsed, document_id, db)
        await self._index_summary(parsed, document_id, doc_uuid, document_name, tenant_id)

        logger.info(
            f"[Doc {document_id}] Summarised as '{parsed.get('doc_type')}' "
            f"covering '{parsed.get('date_range')}'"
        )
        return parsed

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _call_llm(
        self, text: str, document_id: int
    ) -> dict[str, Any] | None:
        """Call the LLM and parse the JSON response."""
        try:
            response = await self._client.chat.completions.create(
                model=self._model,
                messages=[
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {
                        "role": "user",
                        "content": _USER_TEMPLATE.format(
                            char_count=len(text), text=text
                        ),
                    },
                ],
                temperature=0.1,
                max_tokens=600,
            )
            raw = response.choices[0].message.content.strip()
            parsed = json.loads(raw)
            return parsed
        except json.JSONDecodeError as exc:
            logger.warning(
                f"[Doc {document_id}] LLM returned non-JSON summary response: {exc}"
            )
            return None
        except Exception as exc:
            logger.error(
                f"[Doc {document_id}] LLM summarisation failed: {exc}",
                exc_info=True,
            )
            return None

    async def _persist_to_db(
        self,
        parsed: dict[str, Any],
        document_id: int,
        db: AsyncSession,
    ) -> None:
        """Write summary fields to the documents table."""
        try:
            document = await db.get(Document, document_id)
            if not document:
                logger.warning(
                    f"[Doc {document_id}] Document not found when persisting summary."
                )
                return

            # Build a human-readable full summary that includes key topics.
            topics = parsed.get("key_topics") or []
            entities = parsed.get("key_entities") or []
            parts = [parsed.get("summary", "")]
            if topics:
                parts.append("Key topics: " + "; ".join(topics))
            if entities:
                parts.append("Key entities: " + "; ".join(entities))

            document.summary = "\n\n".join(p for p in parts if p)
            document.doc_category = parsed.get("doc_type")
            document.doc_date_range = parsed.get("date_range")
            await db.commit()
        except Exception as exc:
            logger.error(
                f"[Doc {document_id}] Failed to persist summary to DB: {exc}",
                exc_info=True,
            )

    async def _index_summary(
        self,
        parsed: dict[str, Any],
        document_id: int,
        doc_uuid: str,
        document_name: str,
        tenant_id: int,
    ) -> None:
        """Generate a summary embedding and store it in the summaries collection."""
        from app.services.vector_store.factory import VectorStoreFactory, VectorStoreType

        # Build a rich text for embedding: type + date + topics + summary.
        topics = parsed.get("key_topics") or []
        entities = parsed.get("key_entities") or []
        embed_text = "\n".join(
            filter(
                None,
                [
                    f"{parsed.get('doc_type', '')} – {parsed.get('date_range', '')}",
                    parsed.get("summary", ""),
                    "Topics: " + "; ".join(topics) if topics else "",
                    "Entities: " + "; ".join(entities) if entities else "",
                ],
            )
        )

        try:
            embeddings = await self._embedding_service.generate_embeddings([embed_text])
            if not embeddings:
                return

            vector_store = VectorStoreFactory.create(
                VectorStoreType(settings.VECTOR_STORE_TYPE)
            )

            if hasattr(vector_store, "add_document_summary"):
                await vector_store.add_document_summary(
                    document_id=document_id,
                    doc_uuid=doc_uuid,
                    document_name=document_name,
                    tenant_id=tenant_id,
                    summary_text=embed_text,
                    embedding=embeddings[0],
                    metadata={
                        "doc_category": parsed.get("doc_type"),
                        "doc_date_range": parsed.get("date_range"),
                        "summary": parsed.get("summary", ""),
                    },
                )
            else:
                logger.debug(
                    "Vector store does not support summary indexing; skipping."
                )
        except Exception as exc:
            logger.error(
                f"[Doc {document_id}] Failed to index summary embedding: {exc}",
                exc_info=True,
            )
