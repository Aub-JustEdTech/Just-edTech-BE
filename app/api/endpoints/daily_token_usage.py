"""
API endpoints for daily token usage statistics.
"""

from datetime import date, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.crud.daily_token_usage import daily_token_usage
from app.models.users import User
from app.schemas.daily_token_usage import (
    AggregationResultResponse,
    BackfillResultResponse,
    DailyTokenUsageListResponse,
    DailyTokenUsageResponse,
    UsageSummaryResponse,
)
from app.tasks.token_aggregation_tasks import (
    aggregate_daily_token_usage_task,
    backfill_daily_token_usage_task,
)
from app.utils.dependencies import get_current_tenant_user, get_db
from app.utils.response import success_response

router = APIRouter()


@router.get("/daily", response_model=DailyTokenUsageListResponse)
async def get_daily_token_usage(
    start_date: date = Query(..., description="Start date (inclusive)"),
    end_date: date = Query(..., description="End date (inclusive)"),
    model_name: str | None = Query(None, description="Filter by model name"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_tenant_user),
):
    """
    Get daily token usage records for the current tenant within a date range.

    - **start_date**: Start date (inclusive)
    - **end_date**: End date (inclusive)
    - **model_name**: Optional filter by model name
    """
    if start_date > end_date:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="start_date must be before or equal to end_date",
        )

    # Limit query to reasonable date range (e.g., 1 year)
    max_days = 365
    if (end_date - start_date).days > max_days:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Date range cannot exceed {max_days} days",
        )

    records = await daily_token_usage.get_daily_usage_by_tenant(
        db=db,
        tenant_id=current_user.tenant.id,
        start_date=start_date,
        end_date=end_date,
        model_name=model_name,
    )

    response_data = DailyTokenUsageListResponse(
        items=[DailyTokenUsageResponse.model_validate(record) for record in records],
        total=len(records),
    )
    return success_response(data=response_data)


@router.get("/summary", response_model=UsageSummaryResponse)
async def get_usage_summary(
    start_date: date = Query(..., description="Start date (inclusive)"),
    end_date: date = Query(..., description="End date (inclusive)"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_tenant_user),
):
    """
    Get aggregated usage summary for the current tenant within a date range.

    Returns total tokens by model and overall totals.

    - **start_date**: Start date (inclusive)
    - **end_date**: End date (inclusive)
    """
    if start_date > end_date:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="start_date must be before or equal to end_date",
        )

    # Limit query to reasonable date range
    max_days = 365
    if (end_date - start_date).days > max_days:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Date range cannot exceed {max_days} days",
        )

    summary = await daily_token_usage.get_usage_summary(
        db=db,
        tenant_id=current_user.tenant.id,
        start_date=start_date,
        end_date=end_date,
    )

    return success_response(data=UsageSummaryResponse(**summary))


@router.post(
    "/aggregate",
    response_model=AggregationResultResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def trigger_daily_aggregation(
    target_date: date | None = Query(
        None, description="Date to aggregate (defaults to yesterday)"
    ),
    current_user: User = Depends(get_current_tenant_user),
):
    """
    Manually trigger daily token usage aggregation for a specific date.

    This is useful for re-aggregating a specific date or catching up on missed aggregations.
    By default, aggregates yesterday's data.

    - **target_date**: Date to aggregate (defaults to yesterday)

    Returns a task ID that can be used to check the aggregation status.
    """
    # Default to yesterday if no date provided
    if target_date is None:
        target_date = (datetime.utcnow() - timedelta(days=1)).date()

    # Don't allow future dates
    if target_date > datetime.utcnow().date():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot aggregate future dates",
        )

    # Trigger the Celery task
    task = aggregate_daily_token_usage_task.apply_async(
        args=[target_date.isoformat()],
    )

    return success_response(
        data={
            "task_id": task.id,
            "target_date": target_date.isoformat(),
            "status": "Task queued for processing",
        },
        status_code=status.HTTP_202_ACCEPTED,
    )


@router.post(
    "/backfill",
    response_model=BackfillResultResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def trigger_backfill(
    start_date: date = Query(..., description="Start date for backfill"),
    end_date: date = Query(..., description="End date for backfill"),
    current_user: User = Depends(get_current_tenant_user),
):
    """
    Manually trigger backfill of daily token usage data for a date range.

    This processes all dates in the range and aggregates their token usage.
    Useful for populating historical data or fixing missing aggregations.

    - **start_date**: Start date (inclusive)
    - **end_date**: End date (inclusive)

    Returns a task ID that can be used to check the backfill status.
    """
    if start_date > end_date:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="start_date must be before or equal to end_date",
        )

    # Don't allow future dates
    if end_date > datetime.utcnow().date():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot backfill future dates",
        )

    # Limit backfill to reasonable range (e.g., 90 days at a time)
    max_days = 90
    if (end_date - start_date).days > max_days:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Backfill range cannot exceed {max_days} days. Please split into smaller ranges.",
        )

    # Trigger the Celery task
    task = backfill_daily_token_usage_task.apply_async(
        args=[start_date.isoformat(), end_date.isoformat()],
    )

    return success_response(
        data={
            "task_id": task.id,
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
            "status": "Task queued for processing",
        },
        status_code=status.HTTP_202_ACCEPTED,
    )


@router.get("/current-month", response_model=UsageSummaryResponse)
async def get_current_month_usage(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_tenant_user),
):
    """
    Get usage summary for the current month.

    Convenience endpoint that returns aggregated usage for the current calendar month.
    """
    now = datetime.utcnow()
    start_date = date(now.year, now.month, 1)
    end_date = now.date()

    summary = await daily_token_usage.get_usage_summary(
        db=db,
        tenant_id=current_user.tenant.id,
        start_date=start_date,
        end_date=end_date,
    )

    return success_response(data=UsageSummaryResponse(**summary))


@router.get("/last-30-days", response_model=UsageSummaryResponse)
async def get_last_30_days_usage(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_tenant_user),
):
    """
    Get usage summary for the last 30 days.

    Convenience endpoint that returns aggregated usage for the past 30 days.
    """
    end_date = datetime.utcnow().date()
    start_date = end_date - timedelta(days=30)

    summary = await daily_token_usage.get_usage_summary(
        db=db,
        tenant_id=current_user.tenant.id,
        start_date=start_date,
        end_date=end_date,
    )

    return success_response(data=UsageSummaryResponse(**summary))
