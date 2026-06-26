"""
Service to manage tenant user invitations: create, email, validate, accept.
"""

import secrets
from urllib.parse import quote_plus

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.crud.invitations import invitations
from app.db.redis_connector import redis_manager
from app.models.roles import Role
from app.utils.email import send_email


class InvitationService:
    async def create_and_send(
        self,
        db: AsyncSession,
        *,
        tenant_id: int,
        email: str,
        role_id: int | None,
        enforce_tenant_user: bool = True,
    ) -> bool:
        """Create or resend an invitation.

        When enforce_tenant_user=True (default), the stored role is always overridden to
        tenant_user regardless of role_id — used by tenant_admin bulk-invite flow.
        When enforce_tenant_user=False, role_id is used as-is — used by super_admin
        invite-admin flow.
        """
        if await redis_manager.is_invite_on_cooldown(email):
            return False

        if enforce_tenant_user:
            res = await db.execute(select(Role.id).where(Role.name == "tenant_user"))
            role_id = res.scalar_one_or_none() or role_id

        existing_inv = await invitations.get_active_by_email_tenant(
            db, tenant_id=tenant_id, email=email
        )

        if existing_inv:
            token_exists = await redis_manager.exists_invite_token(existing_inv.token)
            if token_exists:
                token = existing_inv.token
            else:
                token = secrets.token_urlsafe(32)
                await invitations.update_token(db, existing_inv.id, token)
                await redis_manager.set_invite_token_ttl(
                    token, settings.INVITATION_EXPIRE_DAYS * 24 * 3600
                )
        else:
            token = secrets.token_urlsafe(32)
            await invitations.create(
                db,
                tenant_id=tenant_id,
                email=email,
                role_id=role_id,
                token=token,
            )
            await redis_manager.set_invite_token_ttl(
                token, settings.INVITATION_EXPIRE_DAYS * 24 * 3600
            )

        invite_link = f"{settings.FRONTEND_BASE_URL.rstrip('/')}/join?token={quote_plus(token)}&email={quote_plus(email)}"
        subject = "You're invited to join Just-EdTech"
        html = (
            f"<p>You've been invited to join Just-EdTech.</p>"
            f"<p>Click the link below to accept the invitation and create your account:</p>"
            f'<p><a href="{invite_link}">Accept Invitation</a></p>'
            f"<p>This invite expires in {settings.INVITATION_EXPIRE_DAYS} days.</p>"
        )
        text = (
            "You've been invited to join Just-EdTech. "
            f"Open this link to accept: {invite_link}\n"
            f"This invite expires in {settings.INVITATION_EXPIRE_DAYS} days."
        )
        sent = send_email(email, subject, html, text)
        if sent:
            await redis_manager.set_invite_cooldown(
                email, settings.INVITATION_RESEND_COOLDOWN_SECONDS
            )
        return sent

    async def validate_token(self, db: AsyncSession, token: str) -> dict | None:
        """Return invite context if token is valid and not accepted and not expired."""
        inv = await invitations.get_by_token(db, token)
        if not inv or inv.accepted:
            return None
        # Token expiry by Redis TTL
        if not await redis_manager.exists_invite_token(token):
            return None
        return {
            "email": inv.email,
            "tenant_id": inv.tenant_id,
            "role_id": inv.role_id,
            "invitation_id": inv.id,
        }

    async def accept(self, db: AsyncSession, token: str) -> dict | None:
        """Mark the token accepted and return its context (non-expired only)."""
        data = await self.validate_token(db, token)
        if not data:
            return None
        await invitations.mark_accepted(db, data["invitation_id"])  # type: ignore[index]
        return data


invitation_service = InvitationService()
