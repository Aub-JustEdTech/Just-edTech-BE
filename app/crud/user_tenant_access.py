"""
CRUD operations for UserTenantAccess — grants tenant_admin users access to specific tenants.
"""

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.tenants import Tenant
from app.models.user_tenant_access import UserTenantAccess
from app.models.users import User


class UserTenantAccessCRUD:
    async def add_access(
        self, db: AsyncSession, *, user_id: int, tenant_id: int
    ) -> UserTenantAccess:
        """Idempotent — no-op if the row already exists."""
        existing = await db.execute(
            select(UserTenantAccess).where(
                UserTenantAccess.user_id == user_id,
                UserTenantAccess.tenant_id == tenant_id,
            )
        )
        row = existing.scalar_one_or_none()
        if row:
            return row

        row = UserTenantAccess(user_id=user_id, tenant_id=tenant_id)
        db.add(row)
        try:
            await db.commit()
            await db.refresh(row)
        except IntegrityError:
            await db.rollback()
            res = await db.execute(
                select(UserTenantAccess).where(
                    UserTenantAccess.user_id == user_id,
                    UserTenantAccess.tenant_id == tenant_id,
                )
            )
            row = res.scalar_one()
        return row

    async def has_access(
        self, db: AsyncSession, *, user_id: int, tenant_id: int
    ) -> bool:
        res = await db.execute(
            select(UserTenantAccess.id).where(
                UserTenantAccess.user_id == user_id,
                UserTenantAccess.tenant_id == tenant_id,
            )
        )
        return res.scalar_one_or_none() is not None

    async def get_tenants_for_user(
        self, db: AsyncSession, *, user_id: int
    ) -> list[Tenant]:
        res = await db.execute(
            select(Tenant)
            .join(UserTenantAccess, UserTenantAccess.tenant_id == Tenant.id)
            .where(UserTenantAccess.user_id == user_id)
        )
        return list(res.scalars().all())

    async def grant_all_existing_tenants_to_user(
        self, db: AsyncSession, *, user_id: int
    ) -> int:
        """Grant access to every existing tenant. Returns count of rows added."""
        tenants_res = await db.execute(select(Tenant.id))
        tenant_ids = list(tenants_res.scalars().all())
        count = 0
        for tid in tenant_ids:
            existing = await db.execute(
                select(UserTenantAccess.id).where(
                    UserTenantAccess.user_id == user_id,
                    UserTenantAccess.tenant_id == tid,
                )
            )
            if existing.scalar_one_or_none() is None:
                db.add(UserTenantAccess(user_id=user_id, tenant_id=tid))
                count += 1
        if count:
            await db.commit()
        return count

    async def grant_new_tenant_to_all_admins(
        self, db: AsyncSession, *, tenant_id: int, tenant_admin_role_id: int
    ) -> int:
        """When a new tenant is created, grant access to all existing tenant_admins."""
        res = await db.execute(
            select(User.id).where(User.role_id == tenant_admin_role_id)
        )
        admin_ids = list(res.scalars().all())
        count = 0
        for uid in admin_ids:
            existing = await db.execute(
                select(UserTenantAccess.id).where(
                    UserTenantAccess.user_id == uid,
                    UserTenantAccess.tenant_id == tenant_id,
                )
            )
            if existing.scalar_one_or_none() is None:
                db.add(UserTenantAccess(user_id=uid, tenant_id=tenant_id))
                count += 1
        if count:
            await db.commit()
        return count


user_tenant_access = UserTenantAccessCRUD()
