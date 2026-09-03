"""District analytics report API.

Generate stakeholder-facing PDF reports for the fixed Q1-Q7 district
queries. Async flow: POST enqueues a Celery task, GET status polls it,
GET download streams the PDF from S3 once ready.
"""

from __future__ import annotations

import io
import logging

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.crud.user_tenant_access import user_tenant_access
from app.crud.users import user
from app.schemas.district_reports import (
    DistrictQueryInfo,
    DistrictReportRequest,
    DistrictReportStatusResponse,
    DistrictReportTaskResponse,
)
from app.schemas.users import User
from app.services.district_report.queries import QUERIES, get_query_spec
from app.utils.dependencies import (
    get_authorized_tenant_id,
    get_current_tenant_user,
    get_db,
)
from app.utils.s3 import S3Manager

router = APIRouter()
logger = logging.getLogger(__name__)


def _s3_manager() -> S3Manager:
    return S3Manager(
        bucket_name=settings.S3_BUCKET_NAME,
        region_name=settings.S3_REGION,
        aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
        aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
    )


@router.get(
    "/queries",
    response_model=list[DistrictQueryInfo],
    summary="List the fixed district analytics queries (Q1-Q7)",
)
async def list_district_queries(
    current_user: User = Depends(get_current_tenant_user),
) -> list[DistrictQueryInfo]:
    """Return the supported fixed query IDs and their titles.

    Used by clients to populate a query picker without free text.
    """
    return [
        DistrictQueryInfo(
            query_id=spec.query_id,
            title=spec.title,
            research_goal=spec.research_goal,
            question=spec.question,
            geography=spec.geography,
        )
        for spec in QUERIES.values()
    ]


@router.post(
    "",
    response_model=DistrictReportTaskResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Start generating a district analytics report PDF for a fixed query",
)
async def create_district_report(
    request: DistrictReportRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_tenant_user),
) -> DistrictReportTaskResponse:
    """Validate the query_id + tenant_id, then enqueue a Celery task.

    Tenant scoping: the request body's `tenant_id` is required and
    authorized against `user_tenant_access`. The JWT's `tenant_id` claim
    is deliberately NOT used, because a single user can be granted access
    to multiple tenants and the caller must pick which one to report on.
    `super_admin` bypasses the access check; `tenant_admin` must have a
    row in `user_tenant_access` for this tenant.

    Poll GET /district-reports/status?task_id=...&tenant_id=... for the
    result, then GET /district-reports/download?task_id=...&tenant_id=...
    to fetch the PDF.
    """
    try:
        get_query_spec(request.query_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc

    # Authorize the body's tenant_id. super_admin bypasses; tenant_admin
    # must have a row in user_tenant_access; non-admins are rejected.
    await _authorize_tenant(current_user, db, request.tenant_id)

    # Import here to avoid the tasks -> models import cycle.
    from app.tasks.district_report_tasks import generate_district_report_task

    task = generate_district_report_task.delay(
        request.tenant_id,
        request.query_id,
        request.chatbot_config_id,
    )
    return DistrictReportTaskResponse(task_id=task.id, status="PENDING")


async def _authorize_tenant(current_user: User, db: AsyncSession, tenant_id: int) -> None:
    """Authorize the caller against a tenant_id via user_tenant_access.

    super_admin bypasses; tenant_admin must have a row; non-admins rejected.
    Mirrors `get_authorized_tenant_id` for the body-field path (POST has a
    JSON body, so the tenant_id comes from the body, not a query param).
    """
    if user.is_super_admin(current_user):
        return
    if user.is_tenant_admin(current_user):
        has = await user_tenant_access.has_access(
            db, user_id=current_user.id, tenant_id=tenant_id
        )
        if not has:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access denied: tenant not in your access list",
            )
        return
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Tenant admin access required",
    )


@router.get(
    "/status",
    response_model=DistrictReportStatusResponse,
    summary="Poll the status of a district report generation task",
)
async def get_district_report_status(
    task_id: str = Query(..., min_length=1, description="Celery task ID to poll."),
    current_user: User = Depends(get_current_tenant_user),
    tenant_id: int = Depends(get_authorized_tenant_id),
) -> DistrictReportStatusResponse:
    from app.celery_app import celery_app

    task_result = celery_app.AsyncResult(task_id)
    if not task_result.ready():
        return DistrictReportStatusResponse(
            running=True, task_status=task_result.state
        )

    raw = task_result.result
    if not isinstance(raw, dict):
        # Worker crash / timeout surfaces as a non-dict result.
        return DistrictReportStatusResponse(
            running=False,
            task_status=task_result.state,
            error=str(raw),
        )

    # Tenant scoping: the caller must have access to the tenant that owns
    # the report. `raw` carries `tenant_id` from the task return value.
    result_tenant_id = raw.get("tenant_id")
    if result_tenant_id is not None and int(result_tenant_id) != int(tenant_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Report belongs to a different tenant",
        )

    if raw.get("error"):
        return DistrictReportStatusResponse(
            running=False,
            task_status=task_result.state,
            error=raw["error"],
        )

    return DistrictReportStatusResponse(
        running=False,
        task_status=task_result.state,
        query_id=raw.get("query_id"),
        report_id=raw.get("report_id"),
        filename=raw.get("filename"),
    )


@router.get(
    "/download",
    summary="Download a generated district report PDF",
    responses={200: {"content": {"application/pdf": {}}}},
)
async def download_district_report(
    task_id: str = Query(..., min_length=1, description="Celery task ID to download."),
    current_user: User = Depends(get_current_tenant_user),
    tenant_id: int = Depends(get_authorized_tenant_id),
) -> StreamingResponse:
    from app.celery_app import celery_app

    task_result = celery_app.AsyncResult(task_id)
    if not task_result.ready():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Report is not ready yet; poll /district-reports/status first.",
        )

    raw = task_result.result
    if not isinstance(raw, dict) or "s3_key" not in raw:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Report task did not produce a downloadable result.",
        )

    result_tenant_id = raw.get("tenant_id")
    if result_tenant_id is not None and int(result_tenant_id) != int(tenant_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Report belongs to a different tenant",
        )

    try:
        pdf_bytes = await _s3_manager().download_bytes(raw["s3_key"])
    except Exception as exc:  # noqa: BLE001
        logger.error("Failed to download district report %s: %s", task_id, exc, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to download report.",
        ) from exc

    filename = raw.get("filename") or "district-report.pdf"
    safe_filename = filename.replace('"', "")

    return StreamingResponse(
        io.BytesIO(pdf_bytes),
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{safe_filename}"',
        },
    )
