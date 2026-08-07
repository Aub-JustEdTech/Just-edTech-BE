"""
Heatmap Generation Engine endpoints.

Two routes under `/api/v1/heatmap/engine`:

- `GET /districts` — chunk-instance counts per district (map view data)
- `GET /districts/{org_code}/citations` — paginated chunk citations for
  a single district

Both are JWT-protected and tenant-scoped via `get_effective_tenant_id`
(the user's own tenant, or an explicit `tenant_id` query param for
admins with access to multiple tenants). They read directly from the
vector store. Districts are keyed by `org_code` (not internal
school/district ids).
"""

from fastapi import APIRouter, Depends, Query

from app.schemas.heatmap_engine import TimeframePreset, TopicCategory
from app.services.heatmap_engine import heatmap_engine_service
from app.utils.dependencies import get_effective_tenant_id
from app.utils.response import success_response

router = APIRouter(prefix="/heatmap/engine", tags=["HeatMap Engine"])

# Default category set: all V1 topic_tags categories (which in turn cover
# all their subtopics). An empty/omitted `categories` param also means
# "all categories" in the service, but we default to the explicit list so
# the OpenAPI docs reflect the actual default behaviour.
_DEFAULT_CATEGORIES: list[TopicCategory] = list(TopicCategory)


@router.get("/districts")
async def list_district_counts(
    timeframe: TimeframePreset = TimeframePreset.MONTH,
    categories: list[TopicCategory] = Query(default=_DEFAULT_CATEGORIES),
    state: str = "MA",
    include_zero: bool = True,
    breakdown: bool = False,
    tenant_id: int = Depends(get_effective_tenant_id),
):
    """Return chunk-instance counts per district for the given filters.

    Defaults to the last month and all topic categories (which include
    their respective subtopics). Pass an explicit `categories` list to
    filter to one or more categories; pass an empty `categories` to
    select all.

    Pass `breakdown=true` to additionally populate `top_category` and
    `top_category_count` on each district with data — the highest-counting
    of the selected categories. This costs extra vector-store counts, so it
    is off by default and intended for the report export rather than the
    map view.

    Each district is identified by `org_code`.
    """
    response = await heatmap_engine_service.count_by_district(
        tenant_id=tenant_id,
        timeframe=timeframe,
        categories=categories,
        state=state,
        include_zero=include_zero,
        breakdown=breakdown,
    )
    return success_response(data=response.model_dump(mode="json"))


@router.get("/districts/{org_code}/citations")
async def get_district_citations(
    org_code: str,
    timeframe: TimeframePreset = TimeframePreset.MONTH,
    categories: list[TopicCategory] = Query(default=_DEFAULT_CATEGORIES),
    page: int = 1,
    page_size: int = Query(default=10, le=25),
    tenant_id: int = Depends(get_effective_tenant_id),
):
    """Return paginated chunk citations for a single district + filters.

    Defaults to the last month and all topic categories (which include
    their respective subtopics). District is looked up by `org_code`.
    """
    citations, meta = await heatmap_engine_service.get_district_citations(
        tenant_id=tenant_id,
        org_code=org_code,
        timeframe=timeframe,
        categories=categories,
        page=page,
        page_size=page_size,
    )
    return success_response(data=citations.model_dump(mode="json"), extra=meta)
