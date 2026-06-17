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


llm_models = LLMModelCRUD()
