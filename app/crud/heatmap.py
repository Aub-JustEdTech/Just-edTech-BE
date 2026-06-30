from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.heatmap import HeatmapKeyword


class HeatmapCRUD:
    async def list_keywords(
        self, db: AsyncSession, tenant_id: int
    ) -> list[HeatmapKeyword]:
        result = await db.execute(
            select(HeatmapKeyword)
            .where(
                HeatmapKeyword.tenant_id == tenant_id,
                HeatmapKeyword.is_active.is_(True),
            )
            .order_by(HeatmapKeyword.sort_order)
        )
        return list(result.scalars().all())


heatmap_crud = HeatmapCRUD()
