"""
CRUD operations for Tenant model.
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.tenants import Tenant


class TenantCRUD:
    """CRUD operations for Tenant model"""

    async def get(self, db: AsyncSession, tenant_id: int) -> Tenant | None:
        return await db.get(Tenant, tenant_id)

    async def get_by_domain(self, db: AsyncSession, domain: str) -> Tenant | None:
        res = await db.execute(select(Tenant).where(Tenant.domain == domain))
        return res.scalar_one_or_none()

    async def get_by_name(self, db: AsyncSession, name: str) -> Tenant | None:
        res = await db.execute(select(Tenant).where(Tenant.name == name))
        return res.scalar_one_or_none()

    async def get_all(self, db: AsyncSession, skip: int = 0, limit: int = 100) -> list[Tenant]:
        res = await db.execute(select(Tenant).offset(skip).limit(limit))
        return list(res.scalars().all())

    async def create(
        self,
        db: AsyncSession,
        *,
        name: str,
        domain: str,
        logo_url: str | None = None,
    ) -> Tenant:
        t = Tenant(name=name, domain=domain, logo_url=logo_url)
        db.add(t)
        await db.commit()
        await db.refresh(t)
        return t


tenant = TenantCRUD()
