"""
CRUD for API keys.
"""

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.api_keys import ApiKey


class ApiKeyCRUD:
    async def create(
        self, db: AsyncSession, *, tenant_id: int, key: str
    ) -> ApiKey:
        api_key = ApiKey(tenant_id=tenant_id, key=key)
        db.add(api_key)
        await db.commit()
        await db.refresh(api_key)
        return api_key

    async def list_by_tenant(self, db: AsyncSession, tenant_id: int) -> list[ApiKey]:
        result = await db.execute(select(ApiKey).where(ApiKey.tenant_id == tenant_id))
        return list(result.scalars().all())

    async def get_by_key(self, db: AsyncSession, key: str) -> ApiKey | None:
        result = await db.execute(select(ApiKey).where(ApiKey.key == key))
        return result.scalar_one_or_none()

    async def get_latest_by_tenant(self, db: AsyncSession, tenant_id: int) -> ApiKey | None:
        """Get the most recently created API key for a tenant"""
        result = await db.execute(
            select(ApiKey)
            .where(ApiKey.tenant_id == tenant_id)
            .order_by(ApiKey.created_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def revoke(self, db: AsyncSession, api_key_id: int, tenant_id: int) -> int:
        result = await db.execute(
            delete(ApiKey).where(ApiKey.id == api_key_id, ApiKey.tenant_id == tenant_id)
        )
        await db.commit()
        return result.rowcount or 0


api_keys = ApiKeyCRUD()
