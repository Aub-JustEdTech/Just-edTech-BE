"""
Invitation endpoints for tenant admins and public validation.
"""

from fastapi import APIRouter, Depends, HTTPException, Path, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.crud.users import user
from app.db.redis_connector import redis_manager
from app.schemas.users import (
    BulkInvitationCreateRequest,
    BulkInvitationResponse,
    User,
)
from app.services.invitation_service import invitation_service
from app.utils.dependencies import get_current_user, get_db
from app.utils.response import success_response

router = APIRouter()


@router.post(
    "/tenants/{tenant_id}/send-invitation",
    status_code=201,
    response_model=BulkInvitationResponse,
)
async def create_invitation(
    tenant_id: int = Path(...),
    payload: BulkInvitationCreateRequest = ...,  # body: list of emails
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    # Authorization: current_user must be admin of the tenant
    if current_user.tenant_id != tenant_id or not user.is_tenant_admin(current_user):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")

    emails = [e.strip().lower() for e in payload.emails]
    results: list[dict[str, str | bool]] = []
    successful = 0
    for e in emails:
        # Check if user already exists
        existing_user = await user.get_by_email(db, e)
        if existing_user:
            # Check if user is already in this tenant
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

        # Check cooldown for better error message
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
