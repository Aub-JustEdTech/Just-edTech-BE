"""
API endpoints for monthly billing and reports.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.crud.monthly_billing import monthly_billing
from app.schemas.monthly_billing import (
    AggregateMonthlyBillingRequest,
    AggregateMonthlyBillingResponse,
    MonthlyBillingResponse,
    MonthlyBillingSummaryResponse,
    YearlyBillingSummaryResponse,
)
from app.tasks.token_aggregation_tasks import aggregate_monthly_billing_task
from app.utils.dependencies import get_db
from app.utils.response import success_response

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get(
    "/monthly-billing/{tenant_id}",
    response_model=list[MonthlyBillingResponse],
    summary="Get monthly billing records for a tenant",
    description="Retrieve monthly billing records for a specific tenant, year, and month.",
)
async def get_monthly_billing(
    tenant_id: int,
    year: int = Query(..., ge=2020, le=2100, description="Billing year"),
    month: int = Query(..., ge=1, le=12, description="Billing month (1-12)"),
    model_name: str | None = Query(None, description="Filter by model name"),
    db: AsyncSession = Depends(get_db),
):
    """
    Get monthly billing records for a specific tenant, year, and month.

    Args:
        tenant_id: Tenant ID
        year: Billing year
        month: Billing month (1-12)
        model_name: Optional filter by model name
        db: Database session

    Returns:
        List of monthly billing records
    """
    try:
        records = await monthly_billing.get_monthly_billing_by_tenant(
            db=db,
            tenant_id=tenant_id,
            year=year,
            month=month,
            model_name=model_name,
        )
        response_data = [
            MonthlyBillingResponse.model_validate(record) for record in records
        ]
        return success_response(data=response_data)
    except Exception as e:
        logger.error(f"Error fetching monthly billing: {e}", exc_info=True)
        raise HTTPException(
            status_code=500, detail=f"Failed to fetch monthly billing: {str(e)}"
        ) from e


@router.get(
    "/monthly-billing/{tenant_id}/summary",
    response_model=MonthlyBillingSummaryResponse,
    summary="Get monthly billing summary for a tenant",
    description="Retrieve aggregated billing summary with breakdown by model for a specific tenant, year, and month.",
)
async def get_monthly_billing_summary(
    tenant_id: int,
    year: int = Query(..., ge=2020, le=2100, description="Billing year"),
    month: int = Query(..., ge=1, le=12, description="Billing month (1-12)"),
    db: AsyncSession = Depends(get_db),
):
    """
    Get aggregated billing summary for a tenant for a specific month.

    Args:
        tenant_id: Tenant ID
        year: Billing year
        month: Billing month (1-12)
        db: Database session

    Returns:
        Billing summary with breakdown by model and totals
    """
    try:
        summary = await monthly_billing.get_billing_summary_by_tenant(
            db=db,
            tenant_id=tenant_id,
            year=year,
            month=month,
        )
        return success_response(data=MonthlyBillingSummaryResponse(**summary))
    except Exception as e:
        logger.error(f"Error fetching monthly billing summary: {e}", exc_info=True)
        raise HTTPException(
            status_code=500, detail=f"Failed to fetch billing summary: {str(e)}"
        ) from e


@router.get(
    "/yearly-billing/{tenant_id}",
    response_model=YearlyBillingSummaryResponse,
    summary="Get yearly billing summary for a tenant",
    description="Retrieve yearly billing summary with monthly breakdown for a specific tenant and year.",
)
async def get_yearly_billing_summary(
    tenant_id: int,
    year: int = Query(..., ge=2020, le=2100, description="Billing year"),
    db: AsyncSession = Depends(get_db),
):
    """
    Get yearly billing summary for a tenant.

    Args:
        tenant_id: Tenant ID
        year: Billing year
        db: Database session

    Returns:
        Yearly billing summary with monthly breakdown
    """
    try:
        summary = await monthly_billing.get_yearly_billing_summary(
            db=db,
            tenant_id=tenant_id,
            year=year,
        )
        return success_response(data=YearlyBillingSummaryResponse(**summary))
    except Exception as e:
        logger.error(f"Error fetching yearly billing summary: {e}", exc_info=True)
        raise HTTPException(
            status_code=500, detail=f"Failed to fetch yearly billing summary: {str(e)}"
        ) from e


@router.post(
    "/monthly-billing/aggregate",
    response_model=AggregateMonthlyBillingResponse,
    summary="Trigger monthly billing aggregation",
    description="Manually trigger the monthly billing aggregation task. If year/month not provided, aggregates previous month.",
    status_code=202,
)
async def trigger_monthly_billing_aggregation(
    request: AggregateMonthlyBillingRequest,
):
    """
    Manually trigger monthly billing aggregation.

    This endpoint triggers a background task to aggregate daily token usage
    into monthly billing records. By default, it aggregates the previous month.

    Args:
        request: Request containing optional year and month

    Returns:
        Task acknowledgment with task ID
    """
    try:
        # Trigger the Celery task
        task = aggregate_monthly_billing_task.apply_async(
            kwargs={
                "year": request.year,
                "month": request.month,
            }
        )

        logger.info(f"Monthly billing aggregation task triggered: {task.id}")

        return success_response(
            data=AggregateMonthlyBillingResponse(
                task_id=task.id,
                status="Task submitted successfully",
                message="Monthly billing aggregation has been queued. Check task status using the task ID.",
            ),
            status_code=status.HTTP_202_ACCEPTED,
        )
    except Exception as e:
        logger.error(
            f"Error triggering monthly billing aggregation: {e}", exc_info=True
        )
        raise HTTPException(
            status_code=500,
            detail=f"Failed to trigger monthly billing aggregation: {str(e)}",
        ) from e


@router.get(
    "/monthly-billing/task/{task_id}",
    summary="Get monthly billing aggregation task status",
    description="Check the status of a monthly billing aggregation task.",
)
async def get_aggregation_task_status(task_id: str):
    """
    Get the status of a monthly billing aggregation task.

    Args:
        task_id: Celery task ID

    Returns:
        Task status and result (if completed)
    """
    try:
        from app.celery_app import celery_app

        task_result = celery_app.AsyncResult(task_id)

        response = {
            "task_id": task_id,
            "status": task_result.state,
        }

        if task_result.ready():
            if task_result.successful():
                response["result"] = task_result.result
            else:
                response["error"] = str(task_result.info)

        return success_response(data=response)
    except Exception as e:
        logger.error(f"Error fetching task status: {e}", exc_info=True)
        raise HTTPException(
            status_code=500, detail=f"Failed to fetch task status: {str(e)}"
        ) from e
