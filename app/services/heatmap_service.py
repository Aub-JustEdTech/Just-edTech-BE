"""
HeatMap service: Qdrant search, Redis caching, presigned S3 URLs, and PDF export.
"""

import json
import logging
import math
from collections import defaultdict
from datetime import timedelta
from io import BytesIO
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.redis_connector import redis_manager
from app.schemas.heatmap import (
    CitationItem,
    CountyCitationsData,
    CountyScoreItem,
    KeywordItem,
    PaginationMeta,
)
from app.services.embeddings.embedding_service import EmbeddingService
from app.services.vector_store.factory import VectorStoreFactory, VectorStoreType
from app.utils.s3 import S3Manager, extract_s3_key_from_url
from app.core.config import settings

logger = logging.getLogger(__name__)

_SNIPPET_MAX_LEN = 200
_CACHE_TTL = timedelta(hours=1)
_SCORE_THRESHOLD = 0.20


def _truncate_snippet(text: str) -> str:
    if len(text) <= _SNIPPET_MAX_LEN:
        return text
    return text[:_SNIPPET_MAX_LEN] + "…"


def _cache_key(tenant_id: int, query: str, state: str) -> str:
    return f"heatmap:{tenant_id}:{query}:{state}"


class HeatmapService:
    """Orchestrates Qdrant search, Redis caching, S3, and PDF for heatmap endpoints."""

    def __init__(self):
        self._embedding_service = EmbeddingService()
        self._vector_store = None
        self._s3_manager = None

    def _get_vector_store(self):
        if self._vector_store is None:
            self._vector_store = VectorStoreFactory.create(VectorStoreType.QDRANT)
        return self._vector_store

    def _get_s3_manager(self) -> S3Manager:
        if self._s3_manager is None:
            self._s3_manager = S3Manager()
        return self._s3_manager

    async def get_heatmap_summary(
        self,
        tenant_id: int,
        query: str,
        state: str,
    ) -> list[CountyScoreItem]:
        """Return per-county intensity scores, using Redis cache when available."""
        key = _cache_key(tenant_id, query, state)

        cached = await redis_manager.get(key)
        if cached:
            try:
                raw = json.loads(cached)
                return [CountyScoreItem(**item) for item in raw]
            except Exception:
                pass

        results = await self._search_heatmap(tenant_id, query)

        serializable = [item.model_dump() for item in results]
        await redis_manager.set(key, serializable, expire=_CACHE_TTL)

        return results

    async def _search_heatmap(
        self,
        tenant_id: int,
        query: str,
    ) -> list[CountyScoreItem]:
        embeddings = await self._embedding_service.generate_embeddings([query])
        if not embeddings:
            return []

        query_vector = embeddings[0]

        chunks = await self._get_vector_store().search(
            query_embedding=query_vector,
            tenant_id=tenant_id,
            limit=1000,
        )

        county_chunks: dict[str, list[dict]] = defaultdict(list)
        for chunk in chunks:
            if chunk.get("score", 0) < _SCORE_THRESHOLD:
                continue
            meta = chunk.get("metadata", {})
            county = meta.get("county")
            if not county:
                continue
            county_chunks[county].append(chunk)

        scores: list[CountyScoreItem] = []
        for county_name, items in county_chunks.items():
            doc_ids = {c.get("metadata", {}).get("document_id") for c in items if c.get("metadata", {}).get("document_id")}
            weighted_score = round(sum(c.get("score", 0) for c in items) * 100)
            scores.append(
                CountyScoreItem(
                    county_name=county_name,
                    intensity_score=weighted_score,
                    conversation_count=len(items),
                    source_count=len(doc_ids),
                )
            )

        scores.sort(key=lambda x: x.intensity_score, reverse=True)
        return scores

    async def get_county_citations(
        self,
        tenant_id: int,
        county: str,
        query: str,
        page: int,
        page_size: int,
    ) -> tuple[CountyCitationsData, PaginationMeta]:
        """Return paginated citations for a specific county + keyword."""
        embeddings = await self._embedding_service.generate_embeddings([query])
        if not embeddings:
            return self._empty_citations(county, query, page, page_size)

        query_vector = embeddings[0]

        chunks = await self._get_vector_store().search(
            query_embedding=query_vector,
            tenant_id=tenant_id,
            limit=1000,
            filters={"county": county},
        )

        county_chunks = [
            c for c in chunks
            if c.get("metadata", {}).get("county") == county
        ]

        county_chunks.sort(key=lambda x: x.get("score", 0), reverse=True)

        total = len(county_chunks)
        total_pages = max(1, math.ceil(total / page_size))
        offset = (page - 1) * page_size
        page_chunks = county_chunks[offset: offset + page_size]

        doc_ids = {c.get("metadata", {}).get("document_id") for c in county_chunks if c.get("metadata", {}).get("document_id")}

        citations: list[CitationItem] = []
        for chunk in page_chunks:
            meta = chunk.get("metadata", {})
            source_url = await self._resolve_source_url(meta.get("s3_url") or meta.get("source_url"))
            citations.append(
                CitationItem(
                    document_title=meta.get("document_name", meta.get("document_id", "")),
                    school_district=meta.get("school_district", ""),
                    date=meta.get("document_date", ""),
                    snippet=_truncate_snippet(chunk.get("text", "")),
                    source_url=source_url,
                    relevance_score=round(chunk.get("score", 0.0), 4),
                )
            )

        data = CountyCitationsData(
            county_name=county,
            keyword=query,
            conversation_count=total,
            source_count=len(doc_ids),
            citations=citations,
        )
        meta_out = PaginationMeta(
            page=page,
            page_size=page_size,
            total=total,
            total_pages=total_pages,
        )
        return data, meta_out

    async def get_county_export_pdf(
        self,
        tenant_id: int,
        county: str,
        query: str,
    ) -> bytes:
        """Generate a PDF of all citations for county + keyword, return bytes."""
        data, _ = await self.get_county_citations(
            tenant_id=tenant_id,
            county=county,
            query=query,
            page=1,
            page_size=10_000,
        )
        return self._build_pdf(data)

    def _build_pdf(self, data: CountyCitationsData) -> bytes:
        """Build a ReportLab PDF from citation data."""
        try:
            from reportlab.lib import colors
            from reportlab.lib.pagesizes import A4
            from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
            from reportlab.lib.units import cm
            from reportlab.platypus import (
                Paragraph,
                SimpleDocTemplate,
                Spacer,
                Table,
                TableStyle,
            )
        except ImportError as e:
            raise RuntimeError("reportlab is required for PDF export — install it via: pip install reportlab") from e

        buf = BytesIO()
        doc = SimpleDocTemplate(buf, pagesize=A4, rightMargin=2 * cm, leftMargin=2 * cm, topMargin=2 * cm, bottomMargin=2 * cm)
        styles = getSampleStyleSheet()
        story = []

        story.append(Paragraph(f"{data.county_name} County — Citation Export", styles["Title"]))
        story.append(Spacer(1, 0.3 * cm))
        story.append(Paragraph(f"Keyword: <b>{data.keyword}</b>", styles["Normal"]))
        story.append(Paragraph(f"Conversations: {data.conversation_count}  |  Sources: {data.source_count}", styles["Normal"]))
        story.append(Spacer(1, 0.5 * cm))

        for i, citation in enumerate(data.citations, start=1):
            story.append(Paragraph(f"<b>SOURCE {i}</b>", styles["Heading3"]))
            story.append(Paragraph(f"<b>{citation.document_title}</b>", styles["Normal"]))
            story.append(Paragraph(f"School District: {citation.school_district}  |  Date: {citation.date}", styles["Normal"]))
            story.append(Paragraph(citation.snippet, ParagraphStyle("Snippet", parent=styles["Normal"], leftIndent=12, rightIndent=12, backColor=colors.HexColor("#f5f5f5"))))
            story.append(Paragraph(f'Source: <a href="{citation.source_url}">{citation.source_url}</a>', styles["Normal"]))
            story.append(Spacer(1, 0.4 * cm))

        doc.build(story)
        return buf.getvalue()

    async def invalidate_heatmap_cache(self, tenant_id: int) -> None:
        """Delete all Redis keys matching heatmap:{tenant_id}:* ."""
        try:
            r = await redis_manager.get_redis()
            pattern = f"heatmap:{tenant_id}:*"
            cursor = 0
            keys_to_delete: list[str] = []
            while True:
                cursor, keys = await r.scan(cursor, match=pattern, count=100)
                keys_to_delete.extend(keys)
                if cursor == 0:
                    break
            if keys_to_delete:
                await r.delete(*keys_to_delete)
                logger.info(f"Invalidated {len(keys_to_delete)} heatmap cache keys for tenant {tenant_id}")
        except Exception as exc:
            logger.warning(f"Failed to invalidate heatmap cache for tenant {tenant_id}: {exc}")

    async def _resolve_source_url(self, raw_url: str | None) -> str:
        if not raw_url:
            return ""
        try:
            s3_key = extract_s3_key_from_url(raw_url, settings.AWS_S3_BUCKET_NAME)
            if s3_key:
                return await self._get_s3_manager().get_presigned_url(s3_key, expiration=3600)
        except Exception:
            pass
        return raw_url or ""

    @staticmethod
    def _empty_citations(county: str, query: str, page: int, page_size: int) -> tuple[CountyCitationsData, PaginationMeta]:
        return (
            CountyCitationsData(county_name=county, keyword=query, conversation_count=0, source_count=0, citations=[]),
            PaginationMeta(page=page, page_size=page_size, total=0, total_pages=1),
        )


heatmap_service = HeatmapService()
