"""
CRUD for LLM models.
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.llm_models import LLMModel


class LLMModelCRUD:
    async def list(self, db: AsyncSession) -> list[LLMModel]:
        """List all LLM models (global)"""
        result = await db.execute(select(LLMModel))
        return list(result.scalars().all())

    async def get(self, db: AsyncSession, model_id: int) -> LLMModel | None:
        """Get a specific LLM model by ID"""
        result = await db.execute(select(LLMModel).where(LLMModel.id == model_id))
        return result.scalar_one_or_none()

    async def get_by_name(self, db: AsyncSession, name: str) -> LLMModel | None:
        """Get a specific LLM model by name"""
        result = await db.execute(select(LLMModel).where(LLMModel.name == name))
        return result.scalar_one_or_none()


llm_models = LLMModelCRUD()
