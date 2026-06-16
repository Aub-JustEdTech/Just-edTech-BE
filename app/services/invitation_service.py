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
    ) -> bool:
        """Create or resend an invitation. Reuses existing valid tokens, creates new ones if expired."""
        # Cooldown to prevent spam - return False if on cooldown (no email sent)
        if await redis_manager.is_invite_on_cooldown(email):
            return False

        # Check for existing active invitation (not accepted)
        existing_inv = await invitations.get_active_by_email_tenant(
            db, tenant_id=tenant_id, email=email
        )

        # If active invitation exists, check if token is still valid in Redis
        if existing_inv:
            token_exists = await redis_manager.exists_invite_token(existing_inv.token)
            if token_exists:
                # Reuse existing valid token and resend email
                token = existing_inv.token
            else:
                # Token expired in Redis, update existing invitation with a new token
                token = secrets.token_urlsafe(32)
                # Enforce tenant_user role
                res = await db.execute(
                    select(Role.id).where(Role.name == "tenant_user")
                )
                tenant_user_role_id = res.scalar_one_or_none()
                enforced_role_id = tenant_user_role_id or role_id
                # Keep role as tenant_user (only update token)
                await invitations.update_token(db, existing_inv.id, token)
                # Set TTL for new token
                await redis_manager.set_invite_token_ttl(
                    token, settings.INVITATION_EXPIRE_DAYS * 24 * 3600
                )
        else:
            # No existing invitation, create new one
            token = secrets.token_urlsafe(32)
            # Enforce tenant_user role
            res = await db.execute(select(Role.id).where(Role.name == "tenant_user"))
            tenant_user_role_id = res.scalar_one_or_none()
            enforced_role_id = tenant_user_role_id or role_id
            await invitations.create(
                db,
                tenant_id=tenant_id,
                email=email,
                role_id=enforced_role_id,
                token=token,
            )
            # Set TTL for token (expiry)
            await redis_manager.set_invite_token_ttl(
                token, settings.INVITATION_EXPIRE_DAYS * 24 * 3600
            )

        # Send invitation email
        invite_link = f"{settings.FRONTEND_BASE_URL.rstrip('/')}/join?token={quote_plus(token)}&email={quote_plus(email)}"
        subject = "You're invited to join a Just-EdTech tenant"
        html = (
            f"<p>You've been invited to join a tenant on Just-EdTech.</p>"
            f"<p>Click the link below to accept the invitation and create your account:</p>"
            f'<p><a href="{invite_link}">Accept Invitation</a></p>'
            f"<p>This invite expires in {settings.INVITATION_EXPIRE_DAYS} days.</p>"
        )
        text = (
            "You've been invited to join a Just-EdTech tenant. "
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
