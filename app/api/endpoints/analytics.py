"""
Analytics API endpoints for token usage and cost tracking.
"""


from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.utils.dependencies import get_db
from app.utils.response import success_response
from app.utils.token_analytics import token_analytics

router = APIRouter()


@router.get("/users/{user_id}/token-usage")
async def get_user_token_usage(
    user_id: int, tenant_id: int | None = None, db: AsyncSession = Depends(get_db)
):
    """
    Get token usage statistics for a specific user.

    Args:
        user_id: User ID to analyze
        tenant_id: Optional tenant ID to filter by
        db: Database session

    Returns:
        Token usage statistics for the user
    """
    try:
        stats = await token_analytics.get_user_token_usage(
            db=db, user_id=user_id, tenant_id=tenant_id
        )
        return success_response(data=stats)
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Error retrieving user token usage: {str(e)}"
        ) from e


@router.get("/tenants/{tenant_id}/token-usage")
async def get_tenant_token_usage(tenant_id: int, db: AsyncSession = Depends(get_db)):
    """
    Get token usage statistics for a specific tenant.

    Args:
        tenant_id: Tenant ID to analyze
        db: Database session

    Returns:
        Token usage statistics for the tenant
    """
    try:
        stats = await token_analytics.get_tenant_token_usage(db=db, tenant_id=tenant_id)
        return success_response(data=stats)
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Error retrieving tenant token usage: {str(e)}"
        ) from e


@router.get("/token-usage/by-model")
async def get_token_usage_by_model(
    tenant_id: int | None = None, db: AsyncSession = Depends(get_db)
):
    """
    Get token usage breakdown by model.

    Args:
        tenant_id: Optional tenant ID to filter by
        db: Database session

    Returns:
        List of model usage statistics
    """
    try:
        stats = await token_analytics.get_token_usage_by_model(
            db=db, tenant_id=tenant_id
        )
        return success_response(data={"models": stats})
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Error retrieving model usage: {str(e)}"
        ) from e
