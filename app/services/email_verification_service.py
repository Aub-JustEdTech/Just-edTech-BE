"""
Service for managing email verification tokens and sending verification emails.
"""

import random
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.redis_connector import redis_manager
from app.models.signups import Signup
from app.models.users import User
from app.utils.email import send_email


class EmailVerificationService:
    """Create and verify email via 6-digit codes"""

    async def send_verification_code(self, db: AsyncSession, user_id: int) -> bool:
        """Generate a 6-digit code, store in Redis against email, and send it."""
        result = await db.execute(select(User).where(User.id == user_id))
        db_user = result.scalar_one_or_none()
        if not db_user:
            return False

        code = f"{random.randint(0, 999999):06d}"
        expires_in_seconds = settings.EMAIL_VERIFICATION_EXPIRE_HOURS * 3600

        ok = await redis_manager.set_email_verification_code(
            email=db_user.email, code=code, expires_in_seconds=expires_in_seconds
        )
        if not ok:
            return False

        subject = "Your verification code"
        html = (
            f"<p>Hi {db_user.name or ''},</p>"
            f"<p>Your verification code is:</p>"
            f'<p style="font-size:20px;font-weight:bold;letter-spacing:4px;">{code}</p>'
            f"<p>This code expires in {settings.EMAIL_VERIFICATION_EXPIRE_HOURS} hours.</p>"
        )
        text = (
            f"Hi {db_user.name or ''},\n\nYour verification code is: {code}\n"
            f"This code expires in {settings.EMAIL_VERIFICATION_EXPIRE_HOURS} hours."
        )
        return send_email(db_user.email, subject, html, text)

    async def send_verification_code_to_email(
        self, email: str, name: str | None
    ) -> bool:
        """Generate a 6-digit code, store in Redis against email, and send it (no DB lookup)."""
        code = f"{random.randint(0, 999999):06d}"
        expires_in_seconds = settings.EMAIL_VERIFICATION_EXPIRE_HOURS * 3600
        ok = await redis_manager.set_email_verification_code(
            email=email, code=code, expires_in_seconds=expires_in_seconds
        )
        if not ok:
            return False

        subject = "Your verification code"
        html = (
            f"<p>Hi {name or ''},</p>"
            f"<p>Your verification code is:</p>"
            f'<p style="font-size:20px;font-weight:bold;letter-spacing:4px;">{code}</p>'
            f"<p>This code expires in {settings.EMAIL_VERIFICATION_EXPIRE_HOURS} hours.</p>"
        )
        text = (
            f"Hi {name or ''},\n\nYour verification code is: {code}\n"
            f"This code expires in {settings.EMAIL_VERIFICATION_EXPIRE_HOURS} hours."
        )
        return send_email(email, subject, html, text)

    async def verify_code(self, db: AsyncSession, email: str, code: str) -> bool:
        """Validate and consume the code for the email only.

        In the new flow, user is created later during tenant setup, so we do not
        attempt to mark a non-existent user verified here.
        """
        ok = await redis_manager.consume_email_verification_code(email, code)
        if not ok:
            return False

        # Mark signup verified if exists
        result = await db.execute(select(Signup).where(Signup.email == email))
        signup_row = result.scalar_one_or_none()
        if signup_row and not getattr(signup_row, "is_verified", False):
            signup_row.is_verified = True
            signup_row.verified_at = datetime.utcnow()
            await db.commit()
            await db.refresh(signup_row)
        return True

    async def resend_code(
        self, db: AsyncSession, email: str
    ) -> tuple[bool, int | None]:
        """Resend a verification code with cooldown enforcement.

        Returns (success, cooldown_remaining_seconds_if_blocked)
        """
        # If user exists and already verified, return idempotent success
        result = await db.execute(select(User).where(User.email == email))
        db_user = result.scalar_one_or_none()
        if db_user and getattr(db_user, "email_verified", False):
            return (True, None)

        # Also if signup exists and is already verified, return success
        if not db_user:
            sres = await db.execute(select(Signup).where(Signup.email == email))
            signup_row = sres.scalar_one_or_none()
            if signup_row and getattr(signup_row, "is_verified", False):
                return (True, None)

        # Cooldown check (applies to both signup and unverified user)
        if await redis_manager.is_resend_on_cooldown(email):
            remaining = await redis_manager.get_resend_cooldown_remaining(email)
            return (False, remaining)

        # Send new code: for pre-user signups, send using email-only; otherwise use user_id path
        if db_user:
            sent = await self.send_verification_code(db, db_user.id)
        else:
            # Check signup exists
            result = await db.execute(select(Signup).where(Signup.email == email))
            signup_row = result.scalar_one_or_none()
            if not signup_row:
                return (False, None)
            sent = await self.send_verification_code_to_email(email, signup_row.name)
        if not sent:
            return (False, None)

        # Start cooldown
        await redis_manager.set_resend_cooldown(
            email, settings.EMAIL_VERIFICATION_RESEND_COOLDOWN_SECONDS
        )
        return (True, None)


email_verification_service = EmailVerificationService()
