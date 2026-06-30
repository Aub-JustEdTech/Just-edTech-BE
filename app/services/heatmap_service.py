import math

from sqlalchemy.ext.asyncio import AsyncSession

from app.crud.heatmap import heatmap_crud
from app.schemas.heatmap import (
    CitationItem,
    DistrictCitationsResponse,
    DistrictScoreItem,
    KeywordItem,
)
from app.services.heatmap_sample_data import (
    KEYWORD_DATA,
    SAMPLE_CITATIONS,
    SAMPLE_DISTRICT_SCORES,
)


class HeatmapService:
    def _resolve(self, query: str) -> tuple[list[DistrictScoreItem], list[CitationItem]]:
        return KEYWORD_DATA.get(query.strip().lower(), (SAMPLE_DISTRICT_SCORES, SAMPLE_CITATIONS))

    async def get_heatmap_summary(
        self, tenant_id: int, query: str, state: str
    ) -> list[DistrictScoreItem]:
        scores, _ = self._resolve(query)
        return scores

    async def get_district_citations(
        self,
        tenant_id: int,
        district: str,
        query: str,
        page: int,
        page_size: int,
    ) -> tuple[DistrictCitationsResponse, dict]:
        _, citations = self._resolve(query)
        total = len(citations)
        start = (page - 1) * page_size
        paginated = citations[start : start + page_size]

        response = DistrictCitationsResponse(
            district_name=district,
            keyword=query,
            conversation_count=total,
            source_count=len({c.document_title for c in citations}),
            citations=paginated,
        )
        meta = {
            "page": page,
            "page_size": page_size,
            "total": total,
            "total_pages": max(1, math.ceil(total / page_size)),
        }
        return response, meta

    async def get_keywords(
        self, db: AsyncSession, tenant_id: int
    ) -> list[KeywordItem]:
        rows = await heatmap_crud.list_keywords(db, tenant_id)
        return [KeywordItem.model_validate(r) for r in rows]


heatmap_service = HeatmapService()
