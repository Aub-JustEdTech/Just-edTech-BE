"""
CRUD operations for Tenant model.
"""

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.tenants import Tenant


class TenantCRUD:
    """CRUD operations for Tenant model"""

    async def get(self, db: AsyncSession, tenant_id: int) -> Tenant | None:
        """Get tenant by ID"""
        return await db.get(Tenant, tenant_id)


tenant = TenantCRUD()
