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

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query

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


def _validate_date_range(start_date: date | None, end_date: date | None) -> None:
    if (start_date is None) != (end_date is None):
        raise HTTPException(
            status_code=400,
            detail="start_date and end_date must be provided together",
        )
    if start_date and end_date and start_date > end_date:
        raise HTTPException(
            status_code=400, detail="start_date must not be after end_date"
        )


@router.get("/districts")
async def list_district_counts(
    timeframe: TimeframePreset = TimeframePreset.MONTH,
    categories: list[TopicCategory] = Query(default=_DEFAULT_CATEGORIES),
    state: str = "MA",
    include_zero: bool = True,
    breakdown: bool = False,
    start_date: date | None = None,
    end_date: date | None = None,
    tenant_id: int = Depends(get_effective_tenant_id),
):
    """Return chunk-instance counts per district for the given filters.

    Defaults to the last month and all topic categories (which include
    their respective subtopics). Pass an explicit `categories` list to
    filter to one or more categories; pass an empty `categories` to
    select all.

    Pass `start_date` and `end_date` (both required together) to use a
    custom day-level date range instead of `timeframe` — the range takes
    precedence when both are supplied.

    Pass `breakdown=true` to additionally populate `top_category`,
    `top_category_count`, and the full `category_counts` breakdown on each
    district with data. This costs extra vector-store counts, so it is off
    by default and intended for the report export rather than the map view.

    Each district is identified by `org_code`.
    """
    _validate_date_range(start_date, end_date)
    response = await heatmap_engine_service.count_by_district(
        tenant_id=tenant_id,
        timeframe=timeframe,
        categories=categories,
        state=state,
        include_zero=include_zero,
        breakdown=breakdown,
        start_date=start_date,
        end_date=end_date,
    )
    return success_response(data=response.model_dump(mode="json"))


@router.get("/districts/{org_code}/citations")
async def get_district_citations(
    org_code: str,
    timeframe: TimeframePreset = TimeframePreset.MONTH,
    categories: list[TopicCategory] = Query(default=_DEFAULT_CATEGORIES),
    page: int = 1,
    page_size: int = Query(default=10, le=25),
    start_date: date | None = None,
    end_date: date | None = None,
    tenant_id: int = Depends(get_effective_tenant_id),
):
    """Return paginated chunk citations for a single district + filters.

    Defaults to the last month and all topic categories (which include
    their respective subtopics). District is looked up by `org_code`. Pass
    `start_date`/`end_date` (both required together) for a custom
    day-level date range instead of `timeframe`.
    """
    _validate_date_range(start_date, end_date)
    citations, meta = await heatmap_engine_service.get_district_citations(
        tenant_id=tenant_id,
        org_code=org_code,
        timeframe=timeframe,
        categories=categories,
        page=page,
        page_size=page_size,
        start_date=start_date,
        end_date=end_date,
    )
    return success_response(data=citations.model_dump(mode="json"), extra=meta)
