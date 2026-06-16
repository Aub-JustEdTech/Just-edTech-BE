"""
LLM Model management endpoints.
"""

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.crud.llm_models import llm_models
from app.schemas.llm_models import LLMModelResponse
from app.utils.dependencies import get_current_tenant_user, get_db

router = APIRouter()


@router.get("/", response_model=list[LLMModelResponse], summary="List LLM models")
async def list_llm_models(
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_tenant_user),
):
    """
    Get list of available LLM models (global).
    """
    models = await llm_models.list(db)
    return models
