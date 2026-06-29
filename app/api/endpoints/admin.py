"""
Admin endpoints for user and tenant management.
These endpoints demonstrate role-based access control.
"""


from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.crud.tenants import tenant as tenant_crud
from app.crud.users import user
from app.models.users import User
from app.schemas.admin import (
    InviteAdminRequest,
    InviteAdminResponse,
    TenantCreateRequest,
    TenantResponse,
)
from app.schemas.users import User as UserSchema
from app.schemas.users import UserCreate, UserUpdate
from app.services.invitation_service import invitation_service
from app.utils.dependencies import (
    get_db,
    require_role,
    require_super_admin,
    require_tenant_access,
)
from app.utils.response import success_response

router = APIRouter()


# Super Admin Only Endpoints
@router.get("/users/all", response_model=list[UserSchema])
async def list_all_users(
    skip: int = 0,
    limit: int = 100,
    db: AsyncSession = Depends(get_db),
    admin: User = require_super_admin,
):
    """List all users across all tenants (Super Admin only)"""
    # Implementation would go here - this is just a placeholder
    # In real implementation, you'd have a method to get all users
    return success_response(data=[])


@router.get("/tenants/all", response_model=list[TenantResponse])
async def list_all_tenants(
    skip: int = 0,
    limit: int = 100,
    db: AsyncSession = Depends(get_db),
    admin: User = require_super_admin,
):
    """List all tenants (Super Admin only)"""
    tenants = await tenant_crud.get_all(db, skip=skip, limit=limit)
    return success_response(data=[TenantResponse.model_validate(t) for t in tenants])


@router.post("/tenants", response_model=TenantResponse, status_code=status.HTTP_201_CREATED)
async def create_tenant(
    payload: TenantCreateRequest,
    db: AsyncSession = Depends(get_db),
    admin: User = require_super_admin,
):
    """Create a new tenant (Super Admin only)"""
    domain = payload.domain or payload.name.lower().replace(" ", "-") + ".local"

    if await tenant_crud.get_by_name(db, payload.name):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A tenant with this name already exists",
        )
    if await tenant_crud.get_by_domain(db, domain):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A tenant with this domain already exists",
        )

    new_tenant = await tenant_crud.create(
        db, name=payload.name, domain=domain, logo_url=payload.logo_url
    )
    return success_response(
        data=TenantResponse.model_validate(new_tenant),
        status_code=status.HTTP_201_CREATED,
    )


@router.post("/tenants/{tenant_id}/invite-admin", response_model=InviteAdminResponse)
async def invite_tenant_admin(
    tenant_id: int,
    payload: InviteAdminRequest,
    db: AsyncSession = Depends(get_db),
    admin: User = require_super_admin,
):
    """Invite a user to become tenant_admin of an existing tenant (Super Admin only)"""
    if not await tenant_crud.get(db, tenant_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Tenant not found"
        )

    existing_user = await user.get_by_email(db, email=payload.email)
    if existing_user:
        return success_response(
            data=InviteAdminResponse(email=payload.email, status="already_member")
        )

    sent = await invitation_service.create_and_send(
        db,
        tenant_id=tenant_id,
        email=payload.email,
        role_id=settings.DEFAULT_ROLE_ID,
        enforce_tenant_user=False,
    )

    return success_response(
        data=InviteAdminResponse(
            email=payload.email,
            status="sent" if sent else "cooldown",
        )
    )


# Tenant Admin or Super Admin Endpoints
@router.get("/tenants/{tenant_id}/users", response_model=list[UserSchema])
async def list_tenant_users(
    tenant_id: int,
    skip: int = 0,
    limit: int = 100,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_tenant_access(check_admin_only=True)),
):
    """List users in a specific tenant (Tenant Admin or Super Admin only)"""
    # Implementation would go here
    return success_response(data=[])


@router.post("/tenants/{tenant_id}/users", response_model=UserSchema)
async def create_tenant_user(
    tenant_id: int,
    user_create: UserCreate,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_tenant_access(check_admin_only=True)),
):
    """Create a new user in a tenant (Tenant Admin or Super Admin only)"""
    # Ensure the user is created in the correct tenant
    user_create.tenant_id = tenant_id

    # Check if user already exists
    existing_user = await user.get_by_email(db, email=user_create.email)
    if existing_user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Email already registered"
        )

    # Create the user
    db_user = await user.create(db, user_create=user_create)

    # Populate role and tenant names for response
    db_user.role_name = user.get_role_name(db_user)
    db_user.tenant_name = user.get_tenant_name(db_user)

    return success_response(
        data=UserSchema.model_validate(db_user),
        status_code=status.HTTP_201_CREATED,
    )


@router.put("/tenants/{tenant_id}/users/{user_id}", response_model=UserSchema)
async def update_tenant_user(
    tenant_id: int,
    user_id: int,
    user_update: UserUpdate,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_tenant_access(check_admin_only=True)),
):
    """Update a user in a tenant (Tenant Admin or Super Admin only)"""
    # Get the user to update
    target_user = await user.get(db, user_id)
    if not target_user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="User not found"
        )

    # Ensure user belongs to the specified tenant (unless super admin)
    if not user.is_super_admin(admin) and target_user.tenant_id != tenant_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User does not belong to this tenant",
        )

    # Update the user
    updated_user = await user.update(db, user_id, user_update)
    if not updated_user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="User not found"
        )

    # Populate role and tenant names for response
    updated_user.role_name = user.get_role_name(updated_user)
    updated_user.tenant_name = user.get_tenant_name(updated_user)

    return success_response(data=UserSchema.model_validate(updated_user))


# Role-specific endpoints (example of using require_role)
@router.get("/tenant-admins")
async def list_tenant_admins(
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_role("super_admin")),
):
    """List all tenant admins (Super Admin only, using require_role)"""
    # Implementation would go here
    return success_response(data=[])


@router.get("/my-tenant-info")
async def get_my_tenant_info(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role("tenant_admin", "super_admin")),
):
    """Get current user's tenant information (Tenant Admin or Super Admin only)"""
    return success_response(
        data={
            "tenant_id": current_user.tenant_id,
            "tenant_name": user.get_tenant_name(current_user),
            "user_role": user.get_role_name(current_user),
            "is_super_admin": user.is_super_admin(current_user),
            "is_tenant_admin": user.is_tenant_admin(current_user),
        }
    )


# Health check endpoint that shows role info
@router.get("/role-info")
async def get_role_info(
    current_user: User = Depends(
        require_role("super_admin", "tenant_admin", "tenant_user")
    ),
):
    """Get current user's role information (Any authenticated user)"""
    return success_response(
        data={
            "user_id": current_user.id,
            "tenant_id": current_user.tenant_id,
            "role": user.get_role_name(current_user),
            "permissions": {
                "is_super_admin": user.is_super_admin(current_user),
                "is_tenant_admin": user.is_tenant_admin(current_user),
                "is_tenant_user": user.is_tenant_user(current_user),
            },
        }
    )
