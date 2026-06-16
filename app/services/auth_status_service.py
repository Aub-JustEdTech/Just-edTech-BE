"""
Service for determining user authentication status and stage.
"""

from sqlalchemy.ext.asyncio import AsyncSession

from app.crud.invitations import invitations
from app.crud.signups import signup
from app.crud.users import user
from app.db.redis_connector import redis_manager
from app.schemas.users import AuthStage, AuthStatusResponse


class AuthStatusService:
    async def get_status_by_email(
        self, db: AsyncSession, email: str
    ) -> AuthStatusResponse:
        normalized_email = email.strip().lower()

        # Check if user exists
        db_user = await user.get_by_email(db, normalized_email)
        if db_user:
            if not getattr(db_user, "email_verified", False):
                return AuthStatusResponse(
                    stage=AuthStage.USER_EXISTS_UNVERIFIED,
                    email=normalized_email,
                    message="User account exists but email is not verified",
                    next_action="verify_email",
                    details={
                        "user_id": db_user.id,
                        "tenant_id": db_user.tenant_id,
                        "email_verified": False,
                    },
                )
            return AuthStatusResponse(
                stage=AuthStage.FULLY_ACTIVE,
                email=normalized_email,
                message="User is fully active and can log in",
                next_action="login",
                details={
                    "user_id": db_user.id,
                    "tenant_id": db_user.tenant_id,
                    "email_verified": True,
                    "role_id": db_user.role_id,
                },
            )

        # Check signup stage
        signup_row = await signup.get_by_email(db, normalized_email)
        if signup_row:
            if not getattr(signup_row, "is_verified", False):
                return AuthStatusResponse(
                    stage=AuthStage.SIGNED_UP_PENDING_VERIFICATION,
                    email=normalized_email,
                    message="Signup exists but email verification is pending",
                    next_action="verify_email",
                    details={
                        "signup_id": signup_row.id,
                        "is_verified": False,
                    },
                )
            verified_pending = await redis_manager.is_verified_pending(normalized_email)
            if verified_pending:
                return AuthStatusResponse(
                    stage=AuthStage.EMAIL_VERIFIED_PENDING_TENANT_SETUP,
                    email=normalized_email,
                    message="Email verified. Tenant setup is required",
                    next_action="setup_tenant",
                    details={
                        "signup_id": signup_row.id,
                        "is_verified": True,
                        "verified_pending": True,
                    },
                )
            return AuthStatusResponse(
                stage=AuthStage.SIGNED_UP_PENDING_VERIFICATION,
                email=normalized_email,
                message="Email verification expired. Please verify again",
                next_action="verify_email",
                details={
                    "signup_id": signup_row.id,
                    "is_verified": True,
                    "verified_pending_expired": True,
                },
            )

        # Check invitations (deterministically choose newest valid token)
        active_invitations = await invitations.get_all_active_by_email(
            db, normalized_email
        )
        for inv in active_invitations:
            token_valid = await redis_manager.exists_invite_token(inv.token)
            if token_valid:
                return AuthStatusResponse(
                    stage=AuthStage.INVITED_PENDING_REGISTRATION,
                    email=normalized_email,
                    message="User has been invited but hasn't registered yet",
                    next_action="register_with_invite",
                    details={
                        "invitation_id": inv.id,
                        "tenant_id": inv.tenant_id,
                        "role_id": inv.role_id,
                        "token_valid": True,
                    },
                )

        # Default
        return AuthStatusResponse(
            stage=AuthStage.NOT_SIGNED_UP,
            email=normalized_email,
            message="User has not signed up yet",
            next_action="register",
            details=None,
        )


auth_status_service = AuthStatusService()
