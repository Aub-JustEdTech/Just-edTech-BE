"""
CRUD operations for HeatMap models.
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.heatmap import CountyDistrictMapping, HeatmapKeyword


class HeatmapCRUD:
    """CRUD operations for heatmap tables."""

    async def get_keywords_by_tenant(
        self, db: AsyncSession, tenant_id: int
    ) -> list[HeatmapKeyword]:
        """Return active keywords for the tenant ordered by sort_order."""
        result = await db.execute(
            select(HeatmapKeyword)
            .where(
                HeatmapKeyword.tenant_id == tenant_id,
                HeatmapKeyword.is_active.is_(True),
            )
            .order_by(HeatmapKeyword.sort_order)
        )
        return result.scalars().all()

    async def get_county_for_district(
        self, db: AsyncSession, district_name: str, state: str = "MA"
    ) -> str | None:
        """Return the county name for a given district name, or None if not found."""
        result = await db.execute(
            select(CountyDistrictMapping.county_name).where(
                CountyDistrictMapping.district_name == district_name,
                CountyDistrictMapping.state == state,
            )
        )
        row = result.scalar_one_or_none()
        return row


heatmap_crud = HeatmapCRUD()
