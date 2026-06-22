"""
HeatMap API endpoints.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.crud.heatmap import heatmap_crud
from app.schemas.heatmap import (
    MA_COUNTY_NAMES,
    CountyCitationsResponse,
    CountyExportParams,
    HeatmapKeywordsResponse,
    HeatmapSummaryResponse,
    KeywordItem,
)
from app.schemas.users import User
from app.services.heatmap_service import heatmap_service
from app.utils.dependencies import get_current_tenant_user, get_db
from app.utils.response import success_response

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/", response_class=None)
async def get_heatmap_summary(
    query: str = Query(..., description="Search keyword"),
    state: str = Query(..., description="State abbreviation — must be 'MA'"),
    current_user: User = Depends(get_current_tenant_user),
):
    """Return per-county intensity scores for a keyword."""
    if not query or not query.strip():
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="query must not be empty")
    if state.upper() != "MA":
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="state must be 'MA'")

    tenant_id = current_user.tenant_id

    scores = await heatmap_service.get_heatmap_summary(
        tenant_id=tenant_id,
        query=query.strip(),
        state=state.upper(),
    )

    return success_response(data=[item.model_dump() for item in scores])


@router.get("/county/export/")
async def export_county_citations_pdf(
    county: str = Query(...),
    query: str = Query(...),
    current_user: User = Depends(get_current_tenant_user),
):
    """Generate and return a PDF of all citations for county + keyword."""
    if not query or not query.strip():
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="query must not be empty")
    if county not in MA_COUNTY_NAMES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"county must be one of: {', '.join(MA_COUNTY_NAMES)}",
        )

    tenant_id = current_user.tenant_id

    pdf_bytes = await heatmap_service.get_county_export_pdf(
        tenant_id=tenant_id,
        county=county,
        query=query.strip(),
    )

    safe_county = county.replace(" ", "_")
    safe_query = query.strip().replace(" ", "_")[:50]
    filename = f"citations_{safe_county}_{safe_query}.pdf"

    return StreamingResponse(
        iter([pdf_bytes]),
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/county/")
async def get_county_citations(
    county: str = Query(...),
    query: str = Query(...),
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1),
    current_user: User = Depends(get_current_tenant_user),
):
    """Return paginated citations for a specific county + keyword."""
    if not query or not query.strip():
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="query must not be empty")
    if county not in MA_COUNTY_NAMES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"county must be one of: {', '.join(MA_COUNTY_NAMES)}",
        )

    page_size = min(page_size, 25)
    tenant_id = current_user.tenant_id

    data, meta = await heatmap_service.get_county_citations(
        tenant_id=tenant_id,
        county=county,
        query=query.strip(),
        page=page,
        page_size=page_size,
    )

    return success_response(
        data=data.model_dump(),
        extra=meta.model_dump(),
    )


@router.get("/keywords/")
async def get_heatmap_keywords(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_tenant_user),
):
    """Return active keyword chips for the authenticated tenant."""
    tenant_id = current_user.tenant_id
    keywords = await heatmap_crud.get_keywords_by_tenant(db=db, tenant_id=tenant_id)
    items = [KeywordItem(id=kw.id, label=kw.label).model_dump() for kw in keywords]
    return success_response(data=items)
