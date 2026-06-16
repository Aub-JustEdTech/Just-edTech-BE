"""
CRUD operations for Tenant model.
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.tenants import Tenant


class TenantCRUD:
    """CRUD operations for Tenant model"""

    async def get(self, db: AsyncSession, tenant_id: int) -> Tenant | None:
        """Get tenant by ID"""
        return await db.get(Tenant, tenant_id)

    async def get_by_domain(self, db: AsyncSession, domain: str) -> Tenant | None:
        """Get tenant by domain"""
        result = await db.execute(select(Tenant).where(Tenant.domain == domain))
        return result.scalar_one_or_none()

    async def get_by_name(self, db: AsyncSession, name: str) -> Tenant | None:
        """Get tenant by name"""
        result = await db.execute(select(Tenant).where(Tenant.name == name))
        return result.scalar_one_or_none()


tenant = TenantCRUD()
