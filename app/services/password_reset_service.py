"""
Service to issue, verify, and consume password reset tokens and send reset emails.
"""

import secrets
from urllib.parse import quote_plus

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.redis_connector import redis_manager
from app.models.users import User
from app.utils.auth import get_password_hash
from app.utils.email import send_email


class PasswordResetService:
    async def issue_reset(self, db: AsyncSession, email: str) -> bool:
        """Generate a secure token and send a password reset email if user exists.

        Returns True even if user does not exist (to avoid enumeration).
        """
        # Check user existence; if not exists, behave as success without sending
        result = await db.execute(select(User).where(User.email == email))
        db_user = result.scalar_one_or_none()
        if not db_user:
            return True

        token = secrets.token_urlsafe(32)
        expires_seconds = settings.PASSWORD_RESET_EXPIRE_HOURS * 3600
        ok = await redis_manager.set_password_reset_token(email, token, expires_seconds)
        if not ok:
            return False

        reset_link = f"{settings.FRONTEND_BASE_URL.rstrip('/')}/reset-password?email={quote_plus(email)}&token={quote_plus(token)}"
        subject = "Reset your password"
        html = (
            f"<p>Hi {db_user.name or ''},</p>"
            f"<p>We received a request to reset your password. Click the link below to reset it:</p>"
            f'<p><a href="{reset_link}">Reset Password</a></p>'
            f"<p>This link expires in {settings.PASSWORD_RESET_EXPIRE_HOURS} hours. If you did not request a reset, you can ignore this email.</p>"
        )
        text = (
            f"Hi {db_user.name or ''},\n\nUse the link to reset your password: {reset_link}\n"
            f"This link expires in {settings.PASSWORD_RESET_EXPIRE_HOURS} hours."
        )
        return send_email(email, subject, html, text)

    async def verify_token(self, email: str, token: str) -> bool:
        """Check if the provided token matches what's stored for the email (non-consuming)."""
        stored = await redis_manager.get_password_reset_token(email)
        return stored is not None and stored == token

    async def consume_and_reset_password(
        self, db: AsyncSession, email: str, token: str, new_password: str
    ) -> bool:
        """Atomically consume the token and update the user's password if valid."""
        ok = await redis_manager.consume_password_reset_token(email, token)
        if not ok:
            return False

        result = await db.execute(select(User).where(User.email == email))
        db_user = result.scalar_one_or_none()
        if not db_user:
            return False

        db_user.password_hash = get_password_hash(new_password)
        await db.commit()
        return True


password_reset_service = PasswordResetService()
