"""Tenant visibility for invite and admin flows."""

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.crud.tenants import tenant as tenant_crud
from app.crud.user_tenant_access import user_tenant_access
from app.crud.users import user
from app.models.tenants import Tenant
from app.models.users import User

SUPER_ADMIN_ROLE_ID = 1


def is_privileged_admin(db_user: User) -> bool:
    """True for super_admin or tenant_admin (by relationship or role_id)."""
    return (
        db_user.role_id == SUPER_ADMIN_ROLE_ID
        or db_user.role_id == settings.DEFAULT_ROLE_ID
        or user.is_super_admin(db_user)
        or user.is_tenant_admin(db_user)
    )


async def list_tenants_for_principal(
    db: AsyncSession,
    current_user: User,
    *,
    skip: int = 0,
    limit: int = 100,
) -> list[Tenant]:
    """Tenants visible to the caller for invite dropdowns and admin lists."""
    if user.is_super_admin(current_user) or current_user.role_id == SUPER_ADMIN_ROLE_ID:
        return await tenant_crud.get_all(db, skip=skip, limit=limit)

    tenants = await user_tenant_access.get_tenants_for_user(
        db, user_id=current_user.id
    )
    if not tenants and (
        user.is_tenant_admin(current_user)
        or current_user.role_id == settings.DEFAULT_ROLE_ID
    ):
        await user_tenant_access.grant_all_existing_tenants_to_user(
            db, user_id=current_user.id
        )
        tenants = await user_tenant_access.get_tenants_for_user(
            db, user_id=current_user.id
        )
    return tenants
