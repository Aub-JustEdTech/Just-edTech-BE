"""
Heatmap service.

Reads precomputed per-(school, topic) aggregates from `heatmap_aggregate`
for the map summary view, and queries Qdrant (filtered by source_id +
topic) for citation drill-down. The heatmap is one pin per charter
school (72 MA charter districts); public-district documents contribute
to citations only.

Behind the `HEATMAP_USE_SAMPLE_DATA` flag, the legacy canned sample data
is served instead — useful for local dev without a populated vector store.
"""

import logging
import math

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.connector import AsyncSessionLocal
from app.models.heatmap_aggregate import HeatmapAggregate
from app.models.school import School
from app.schemas.heatmap import (
    CitationItem,
    DistrictCitationsResponse,
    DistrictScoreItem,
    KeywordItem,
)
from app.services.heatmap_ingest.taxonomy import TOPICS
from app.services.vector_store.factory import VectorStoreFactory, VectorStoreType

logger = logging.getLogger(__name__)

# Map DB district_type values to the API's two-bucket normalized form.
_CHARTER_TYPES = {"charter district", "charter"}


def _normalize_district_type(raw: str | None) -> str:
    if raw and raw.strip().lower() in _CHARTER_TYPES:
        return "charter"
    return "public"


class HeatmapService:
    """Reads heatmap data from heatmap_aggregate + Qdrant."""

    async def get_heatmap_summary(
        self, tenant_id: int, query: str, state: str
    ) -> list[DistrictScoreItem]:
        """
        Return one DistrictScoreItem per school that has any aggregate
        rows for the queried topic.

        The query is matched against the TOPICS taxonomy (case-insensitive
        substring). Pin = charter school; public districts appear too but
        don't drive the map metric.

        If HEATMAP_USE_SAMPLE_DATA is True, returns the canned sample data.
        """
        if getattr(settings, "HEATMAP_USE_SAMPLE_DATA", False):
            from app.services.heatmap_sample_data import (
                KEYWORD_DATA,
                SAMPLE_DISTRICT_SCORES,
            )

            scores, _ = KEYWORD_DATA.get(
                query.strip().lower(),
                (SAMPLE_DISTRICT_SCORES, {}),
            )
            return scores

        topic = _resolve_topic(query)
        if topic is None:
            return []

        async with AsyncSessionLocal() as db:
            # Join heatmap_aggregate with schools to get the name + type.
            # Only return rows for this tenant + topic.
            rows = (
                await db.execute(
                    select(HeatmapAggregate, School)
                    .join(School, School.id == HeatmapAggregate.source_id)
                    .where(
                        School.tenant_id == tenant_id,
                        HeatmapAggregate.topic == topic,
                    )
                )
            ).all()

            items: list[DistrictScoreItem] = []
            for agg, school in rows:
                # Intensity score: weight chunks + meetings + actions.
                action_total = sum(agg.action_types.values()) if agg.action_types else 0
                intensity = (
                    (agg.chunk_count or 0)
                    + 5 * (agg.meeting_count or 0)
                    + 3 * (agg.doc_count or 0)
                    + 2 * action_total
                )
                items.append(
                    DistrictScoreItem(
                        district_name=school.name,
                        intensity_score=intensity,
                        conversation_count=agg.meeting_count or 0,
                        source_count=agg.doc_count or 0,
                        district_type=_normalize_district_type(school.district_type),
                    )
                )

            # Sort: charter first (they drive the map), then by intensity desc.
            items.sort(
                key=lambda i: (
                    0 if i.district_type == "charter" else 1,
                    -i.intensity_score,
                )
            )
            return items

    async def get_district_citations(
        self,
        tenant_id: int,
        district: str,
        query: str,
        page: int,
        page_size: int,
    ) -> tuple[DistrictCitationsResponse, dict]:
        """
        Return paginated citations for one district + topic.

        Citations are pulled from Qdrant: filter chunks where
        `school_id` matches the district's school row and `topics` contains
        the queried topic. Each chunk is hydrated into a CitationItem
        using the Qdrant payload (document_name, page_number, text,
        source_media_url) plus the Document.meeting_date.
        """
        if getattr(settings, "HEATMAP_USE_SAMPLE_DATA", False):
            return self._sample_citations(district, query, page, page_size)

        topic = _resolve_topic(query)
        if topic is None:
            return self._empty_response(district, query, page, page_size), self._meta(
                page, page_size, 0
            )

        async with AsyncSessionLocal() as db:
            # Resolve the school row by name (case-insensitive) for this tenant.
            school = await self._find_school_by_name(db, tenant_id, district)
            if school is None:
                logger.info(
                    f"No school found for district={district!r}; returning empty citations"
                )
                return self._empty_response(
                    district, query, page, page_size
                ), self._meta(page, page_size, 0)

            # Query Qdrant for chunks tagged with this topic for this school.
            citations = await self._fetch_citations_from_qdrant(
                db=db,
                tenant_id=tenant_id,
                school_id=school.id,
                topic=topic,
                limit=page_size,
                offset=(page - 1) * page_size,
            )

            total = len(citations)
            response = DistrictCitationsResponse(
                district_name=school.name,
                keyword=query,
                conversation_count=total,
                source_count=len({c.document_id for c in citations if c.document_id}),
                citations=citations,
            )
            return response, self._meta(page, page_size, total)

    async def get_keywords(self) -> list[KeywordItem]:
        """Enumerate the frozen TOPICS taxonomy as keyword options."""
        return [
            KeywordItem(id=i + 1, label=topic.replace("_", " ").title())
            for i, topic in enumerate(TOPICS)
        ]

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _find_school_by_name(
        self, db: AsyncSession, tenant_id: int, name: str
    ) -> School | None:
        """Case-insensitive name lookup; falls back to a 'contains' match."""
        from sqlalchemy import func

        # Exact (case-insensitive) match first.
        stmt = (
            select(School)
            .where(
                School.tenant_id == tenant_id,
                func.lower(School.name) == name.strip().lower(),
            )
            .limit(1)
        )
        school = (await db.execute(stmt)).scalar_one_or_none()
        if school is not None:
            return school

        # Fall back to a contains match (handles "(District)" suffix etc.).
        stmt = (
            select(School)
            .where(
                School.tenant_id == tenant_id,
                func.lower(School.name).contains(name.strip().lower()),
            )
            .limit(1)
        )
        return (await db.execute(stmt)).scalar_one_or_none()

    async def _fetch_citations_from_qdrant(
        self,
        *,
        db: AsyncSession,
        tenant_id: int,
        school_id: int,
        topic: str,
        limit: int,
        offset: int,
    ) -> list[CitationItem]:
        """Scroll Qdrant for chunks tagged with `topic` for `school_id`."""
        vector_store = VectorStoreFactory.create(
            VectorStoreType(settings.VECTOR_STORE_TYPE)
        )
        if not hasattr(vector_store, "filter_chunks"):
            logger.warning(
                "Vector store %s does not support filter_chunks; "
                "returning empty citations",
                type(vector_store).__name__,
            )
            return []

        # Qdrant scrolls in pages; we apply our own offset/limit on top.
        # Fetch enough to cover the requested page.
        fetch_limit = offset + limit
        chunks = await vector_store.filter_chunks(
            tenant_id=tenant_id,
            must_match={"school_id": school_id, "classified": True},
            must_match_any={"topics": [topic]},
            limit=fetch_limit,
        )
        page = chunks[offset : offset + limit]

        # Hydrate each chunk into a CitationItem.
        items: list[CitationItem] = []
        for ch in page:
            meta = ch.get("metadata", {})
            text = ch.get("text", "")
            snippet = text[:300] + ("…" if len(text) > 300 else "")
            items.append(
                CitationItem(
                    document_id=meta.get("document_id"),
                    document_title=meta.get("document_name") or meta.get("school_name") or "Untitled",
                    date=meta.get("meeting_date"),
                    snippet=snippet,
                    source_url=meta.get("source_media_url") or meta.get("source_page_url") or "",
                    relevance_score=float(ch.get("score", 1.0)),
                    page_number=meta.get("page_number"),
                )
            )
        return items

    # -- sample-data fallback (only used when HEATMAP_USE_SAMPLE_DATA=True) --

    def _sample_citations(
        self, district: str, query: str, page: int, page_size: int
    ) -> tuple[DistrictCitationsResponse, dict]:
        from app.services.heatmap_sample_data import (
            KEYWORD_DATA,
            SAMPLE_CITATIONS,
        )
        _, citations_by_district = KEYWORD_DATA.get(
            query.strip().lower(), ([], SAMPLE_CITATIONS)
        )
        citations = citations_by_district.get(district.strip().lower(), [])
        total = len(citations)
        start = (page - 1) * page_size
        paginated = citations[start : start + page_size]
        response = DistrictCitationsResponse(
            district_name=district,
            keyword=query,
            conversation_count=total,
            source_count=len({c.document_id for c in citations if c.document_id}),
            citations=paginated,
        )
        return response, self._meta(page, page_size, total)

    @staticmethod
    def _empty_response(district: str, query: str, page: int, page_size: int) -> DistrictCitationsResponse:
        return DistrictCitationsResponse(
            district_name=district,
            keyword=query,
            conversation_count=0,
            source_count=0,
            citations=[],
        )

    @staticmethod
    def _meta(page: int, page_size: int, total: int) -> dict:
        return {
            "page": page,
            "page_size": page_size,
            "total": total,
            "total_pages": max(1, math.ceil(total / page_size)) if page_size else 1,
        }


def _resolve_topic(query: str) -> str | None:
    """
    Map a free-text query to a canonical TOPICS label.

    Matches case-insensitively; first exact match wins, then a contains
    match. Returns None if nothing matches (caller returns empty list).
    """
    q = query.strip().lower()
    if not q:
        return None
    for t in TOPICS:
        if t.lower() == q:
            return t
    # Allow "sex ed" → "sex_education" style lookups via underscores.
    normalized = q.replace(" ", "_")
    for t in TOPICS:
        if t.lower() == normalized:
            return t
    # Fuzzy contains on the underscore-normalized form
    # (e.g. "sex ed" → "sex_ed" matches "sex_education").
    for t in TOPICS:
        if normalized in t.lower() or t.lower() in normalized:
            return t
    # Fuzzy contains on the raw query (e.g. "censorship" → "curriculum_censorship").
    for t in TOPICS:
        if q in t.lower() or t.lower() in q:
            return t
    return None


heatmap_service = HeatmapService()
