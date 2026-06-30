from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.users import User
from app.services.heatmap_service import heatmap_service
from app.utils.dependencies import get_current_user, get_db
from app.utils.response import success_response

router = APIRouter()


@router.get("/")
async def get_heatmap_summary(
    query: str,
    state: str = "MA",
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    result = await heatmap_service.get_heatmap_summary(
        tenant_id=current_user.tenant_id,
        query=query,
        state=state,
    )
    return success_response(data=[r.model_dump() for r in result])


@router.get("/district/")
async def get_district_summary(
    query: str,
    state: str = "MA",
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    result = await heatmap_service.get_heatmap_summary(
        tenant_id=current_user.tenant_id,
        query=query,
        state=state,
    )
    return success_response(data=[r.model_dump() for r in result])


@router.get("/district/citations/")
async def get_district_citations(
    district: str,
    query: str,
    page: int = 1,
    page_size: int = Query(default=10, le=25),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    citations, meta = await heatmap_service.get_district_citations(
        tenant_id=current_user.tenant_id,
        district=district,
        query=query,
        page=page,
        page_size=page_size,
    )
    return success_response(data=citations.model_dump(), extra=meta)


@router.get("/district/export/")
async def export_district_citations(
    district: str,
    query: str,
    current_user: User = Depends(get_current_user),
):
    return success_response(
        data=None,
        extra={"message": "PDF export not yet implemented"},
        status_code=501,
    )


@router.get("/keywords/")
async def get_heatmap_keywords(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    result = await heatmap_service.get_keywords(db=db, tenant_id=current_user.tenant_id)
    return success_response(data=[r.model_dump() for r in result])
