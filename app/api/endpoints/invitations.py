"""
Invitation endpoints for tenant admins, super admins, and public validation.
"""

from fastapi import APIRouter, Depends, HTTPException, Path, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.crud.user_tenant_access import user_tenant_access
from app.crud.users import user
from app.db.redis_connector import redis_manager
from app.schemas.users import (
    BulkInvitationCreateRequest,
    BulkInvitationResponse,
    UnifiedInvitationRequest,
    User,
)
from app.services.invitation_service import invitation_service
from app.utils.dependencies import get_current_tenant_admin, get_current_user, get_db
from app.utils.response import success_response

router = APIRouter()


@router.post(
    "/tenants/{tenant_id}/send-invitation",
    status_code=201,
    response_model=BulkInvitationResponse,
)
async def create_invitation(
    tenant_id: int = Path(...),
    payload: BulkInvitationCreateRequest = ...,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    is_super = user.is_super_admin(current_user)
    is_admin = user.is_tenant_admin(current_user)

    if not (is_super or is_admin):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")

    # tenant_admin must have access to this tenant; super_admin bypasses
    if is_admin and not is_super:
        has = await user_tenant_access.has_access(
            db, user_id=current_user.id, tenant_id=tenant_id
        )
        if not has:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access denied: tenant not in your access list",
            )

    emails = [e.strip().lower() for e in payload.emails]
    results: list[dict[str, str | bool]] = []
    successful = 0
    for e in emails:
        existing_user = await user.get_by_email(db, e)
        if existing_user:
            if existing_user.tenant_id == tenant_id:
                results.append(
                    {
                        "email": e,
                        "sent": False,
                        "message": "User is already a member of this tenant",
                    }
                )
            else:
                results.append(
                    {
                        "email": e,
                        "sent": False,
                        "message": "User already exists in the system",
                    }
                )
            continue

        if await redis_manager.is_invite_on_cooldown(e):
            remaining = await redis_manager.get_invite_cooldown_remaining(e)
            results.append(
                {
                    "email": e,
                    "sent": False,
                    "message": f"Please wait {remaining} seconds before requesting another invitation",
                }
            )
            continue

        ok = await invitation_service.create_and_send(
            db, tenant_id=tenant_id, email=e, role_id=payload.role_id
        )
        if ok:
            successful += 1
            results.append({"email": e, "sent": True, "message": "Invitation sent"})
        else:
            results.append({"email": e, "sent": False, "message": "Failed to send"})

    return success_response(
        data={
            "total": len(emails),
            "successful": successful,
            "failed": len(emails) - successful,
            "results": results,
        },
        status_code=status.HTTP_201_CREATED,
    )


@router.post("/send", status_code=201, response_model=BulkInvitationResponse)
async def send_unified_invitation(
    payload: UnifiedInvitationRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_tenant_admin),
):
    """Unified invite endpoint for both tenant_admin and tenant_user roles.

    - role_id=2 (tenant_admin): tenant_id ignored; invite grants access to all tenants
    - role_id=3 (tenant_user): tenant_id required; must be accessible by caller
    """
    TENANT_ADMIN_ROLE_ID = 2
    TENANT_USER_ROLE_ID = 3

    is_super = user.is_super_admin(current_user)
    is_admin = user.is_tenant_admin(current_user)

    if payload.role_id == TENANT_USER_ROLE_ID:
        if payload.tenant_id is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="tenant_id is required when inviting members",
            )
        if is_admin and not is_super:
            has = await user_tenant_access.has_access(
                db, user_id=current_user.id, tenant_id=payload.tenant_id
            )
            if not has:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Access denied: tenant not in your access list",
                )

    emails = [e.strip().lower() for e in payload.emails]
    results: list[dict[str, str | bool]] = []
    successful = 0

    for e in emails:
        existing_user = await user.get_by_email(db, e)
        if existing_user:
            results.append(
                {"email": e, "sent": False, "message": "User already exists in the system"}
            )
            continue

        if await redis_manager.is_invite_on_cooldown(e):
            remaining = await redis_manager.get_invite_cooldown_remaining(e)
            results.append(
                {
                    "email": e,
                    "sent": False,
                    "message": f"Please wait {remaining} seconds before requesting another invitation",
                }
            )
            continue

        if payload.role_id == TENANT_ADMIN_ROLE_ID:
            ok = await invitation_service.create_and_send(
                db,
                tenant_id=None,
                email=e,
                role_id=TENANT_ADMIN_ROLE_ID,
                enforce_tenant_user=False,
            )
        else:
            ok = await invitation_service.create_and_send(
                db,
                tenant_id=payload.tenant_id,
                email=e,
                role_id=TENANT_USER_ROLE_ID,
                enforce_tenant_user=True,
            )

        if ok:
            successful += 1
            results.append({"email": e, "sent": True, "message": "Invitation sent"})
        else:
            results.append({"email": e, "sent": False, "message": "Failed to send"})

    return success_response(
        data={
            "total": len(emails),
            "successful": successful,
            "failed": len(emails) - successful,
            "results": results,
        },
        status_code=status.HTTP_201_CREATED,
    )


@router.get("/{token}/validate")
async def validate_invitation(token: str, db: AsyncSession = Depends(get_db)):
    data = await invitation_service.validate_token(db, token)
    if not data:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired invitation",
        )
    return success_response(
        data={
            "email": data["email"],
            "tenant_id": data["tenant_id"],
            "role_id": data["role_id"],
        }
    )
