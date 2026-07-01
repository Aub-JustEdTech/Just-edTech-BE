from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response

from app.models.users import User
from app.services.heatmap_service import heatmap_service
from app.services.pdf_export import generate_citations_pdf
from app.utils.dependencies import get_current_user
from app.utils.response import success_response

router = APIRouter()


@router.get("/")
async def get_heatmap_summary(
    query: str,
    state: str = "MA",
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
    charter_schools: str = "",
    current_user: User = Depends(get_current_user),
):
    sections = []

    public_response, _ = await heatmap_service.get_district_citations(
        tenant_id=current_user.tenant_id,
        district=district,
        query=query,
        page=1,
        page_size=1000,
    )
    sections.append({
        "name": district,
        "type": "public",
        "citations": [c.model_dump() for c in public_response.citations],
    })

    for school_name in (s.strip() for s in charter_schools.split(",") if s.strip()):
        charter_response, _ = await heatmap_service.get_district_citations(
            tenant_id=current_user.tenant_id,
            district=school_name,
            query=query,
            page=1,
            page_size=1000,
        )
        if charter_response.citations:
            sections.append({
                "name": school_name,
                "type": "charter",
                "citations": [c.model_dump() for c in charter_response.citations],
            })

    pdf_bytes = generate_citations_pdf(
        district_name=district,
        keyword=query,
        sections=sections,
    )

    safe_name = f"citations_{district}_{query}.pdf".replace(" ", "_").replace("/", "-")
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{safe_name}"'},
    )


@router.get("/keywords/")
async def get_heatmap_keywords(
    current_user: User = Depends(get_current_user),
):
    result = await heatmap_service.get_keywords()
    return success_response(data=[r.model_dump() for r in result])
