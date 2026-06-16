"""
Authentication endpoints for user registration and login.
"""

from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import (
    HTTPBearer,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.crud.api_keys import api_keys
from app.crud.signups import signup
from app.crud.users import user
from app.db.redis_connector import redis_manager
from app.models.tenants import Tenant
from app.models.users import User as UserModel
from app.schemas.users import (
    AuthStatusRequest,
    AuthStatusResponse,
    ForgotPasswordRequest,
    LoginRequest,
    RefreshTokenRequest,
    ResendVerificationRequest,
    ResetPasswordRequest,
    SetupTenantRequest,
    SignupRequest,
    Token,
    User,
    VerifyEmailRequest,
    VerifyResetTokenRequest,
)
from app.services.auth_status_service import auth_status_service
from app.services.email_verification_service import email_verification_service
from app.services.invitation_service import invitation_service
from app.services.password_reset_service import password_reset_service
from app.services.refresh_token_service import refresh_token_service
from app.utils.api_keys import generate_api_key
from app.utils.auth import create_access_token
from app.utils.dependencies import get_current_user, get_db
from app.utils.response import success_response
from app.utils.s3 import S3Manager

security = HTTPBearer()

router = APIRouter()


@router.post("/register")
async def register(signup_req: SignupRequest, db: AsyncSession = Depends(get_db)):
    """Registration: normal signup (OTP + tenant setup) or invite-based join."""
    from app.utils.auth import get_password_hash

    normalized_email = signup_req.email.strip().lower()

    # If invite flow: create user under tenant and mark invite accepted
    if signup_req.invite_token:
        # Prevent duplicates
        if await user.get_by_email(db, email=normalized_email):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Email already registered",
            )

        data = await invitation_service.validate_token(db, signup_req.invite_token)
        if not data:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid or expired invitation",
            )
        # Email must match invite
        if normalized_email != data["email"].strip().lower():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invitation email mismatch",
            )

        pwd_hash = get_password_hash(signup_req.password)
        # Enforce tenant_user role
        role_id_to_use = data["role_id"]
        if not role_id_to_use:
            await db.execute(select(Tenant).where(Tenant.id == data["tenant_id"]))
            # Although tenant fetch isn't strictly needed, keep select imported context consistent
            from app.models.roles import Role

            res = await db.execute(select(Role.id).where(Role.name == "tenant_user"))
            role_id_to_use = res.scalar_one_or_none() or settings.DEFAULT_TENANT_USER_ID
        new_user = UserModel(
            email=normalized_email,
            name=signup_req.name,
            password_hash=pwd_hash,
            tenant_id=data["tenant_id"],
            role_id=role_id_to_use,
            email_verified=True,
        )
        db.add(new_user)
        await db.commit()
        await db.refresh(new_user)

        # Clean up any existing signup record and related Redis verification data
        await signup.delete_by_email(db, normalized_email)
        # Clean up any pending email verification flags in Redis
        await redis_manager.consume_verified_pending(normalized_email)
        # Mark invite accepted
        await invitation_service.accept(db, signup_req.invite_token)
        return success_response(
            data={"message": "User created via invitation", "user_id": new_user.id}
        )

    # Normal OTP-based signup flow
    if await user.get_by_email(db, email=normalized_email):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Email already registered"
        )
    if await signup.get_by_email(db, email=normalized_email):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Signup already exists"
        )

    pwd_hash = get_password_hash(signup_req.password)
    signup_row = await signup.create(
        db,
        email=normalized_email,
        name=signup_req.name,
        password_hash=pwd_hash,
    )
    await email_verification_service.send_verification_code_to_email(
        signup_row.email, signup_row.name
    )
    return success_response(
        data={"message": "Signup created. Verification code sent."}
    )


@router.post("/login", response_model=Token)
async def login(payload: LoginRequest, db: AsyncSession = Depends(get_db)):
    """Login user and return access and refresh tokens"""
    # Authenticate using email as username
    db_user = await user.authenticate(
        db, email=payload.username, password=payload.password
    )
    if not db_user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Block login if email not verified
    if not getattr(db_user, "email_verified", False):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Email not verified",
        )

    # Create access token with enhanced payload
    access_token_expires = timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token_data = {
        "user_id": db_user.id,  # explicit user_id
        "tenant_id": db_user.tenant_id,
        "role": user.get_role_name(db_user),
    }
    access_token = create_access_token(
        data=access_token_data, expires_delta=access_token_expires
    )

    # Create refresh token in Redis
    refresh_token_str = await refresh_token_service.create_token(db_user.id)

    return success_response(
        data={
            "access_token": access_token,
            "refresh_token": refresh_token_str,
            "token_type": "bearer",
            "expires_in": settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,  # seconds
        }
    )


@router.post("/verify-email")
async def verify_email(payload: VerifyEmailRequest, db: AsyncSession = Depends(get_db)):
    """Verify code then create tenant and user from signup"""
    # Validate code first, then set verified-pending flag

    if not await email_verification_service.verify_code(
        db, payload.email, payload.code
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid or expired code"
        )
    await redis_manager.set_verified_pending(
        payload.email, settings.EMAIL_VERIFICATION_EXPIRE_HOURS * 3600
    )
    return success_response(
        data={"message": "Email verified. Proceed to setup tenant."}
    )


@router.post("/setup-tenant")
async def setup_tenant(payload: SetupTenantRequest, db: AsyncSession = Depends(get_db)):
    """Create tenant and user after OTP verification"""

    if not await redis_manager.is_verified_pending(payload.email):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email not verified or session expired",
        )

    # Fetch signup
    signup_row = await signup.get_by_email(db, payload.email)
    if not signup_row:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Signup not found"
        )

    # Normalize domain and ensure uniqueness
    domain = (
        payload.tenant_domain
        or payload.tenant_name.lower().replace(" ", "-") + ".local"
    )
    domain = domain.strip().lower()

    # Ensure domain is unique

    existing_tenant = await db.execute(select(Tenant).where(Tenant.domain == domain))
    if existing_tenant.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Tenant domain already exists",
        )

    tenant = Tenant(name=payload.tenant_name, domain=domain)
    db.add(tenant)
    await db.flush()

    # If logo provided, upload to S3 and store URL
    if payload.logo_b64:
        import base64
        import binascii

        logo_b64 = payload.logo_b64.strip()
        # Support data URLs: data:image/png;base64,<data>
        if logo_b64.startswith("data:"):
            try:
                logo_b64 = logo_b64.split(",", 1)[1]
            except Exception:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Invalid data URL format for logo_b64",
                )
        # Normalize padding
        missing_padding = (-len(logo_b64)) % 4
        if missing_padding:
            logo_b64 += "=" * missing_padding
        try:
            raw = base64.b64decode(logo_b64, validate=True)
        except (binascii.Error, ValueError):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid base64 in logo_b64",
            )
        if not raw:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Decoded logo_b64 is empty",
            )
        s3_key = f"tenants/{tenant.id}/logo"

        s3 = S3Manager(
            bucket_name=settings.S3_BUCKET_NAME,
            region_name=settings.S3_REGION,
            aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
            aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
        )
        logo_url = await s3.upload_file_object(raw, s3_key)
        tenant.logo_url = logo_url

    new_user = UserModel(
        email=signup_row.email,
        name=signup_row.name,
        password_hash=signup_row.password_hash,
        tenant_id=tenant.id,
        role_id=settings.DEFAULT_ROLE_ID,
        email_verified=True,
    )
    db.add(new_user)
    await db.commit()
    await db.refresh(new_user)

    # Generate a default API key for the tenant and return to creator
    key = generate_api_key()
    record = await api_keys.create(
        db,
        tenant_id=tenant.id,
        key=key,
    )

    # Cleanup pending/ signup
    await signup.delete_by_email(db, payload.email)
    await redis_manager.consume_verified_pending(payload.email)

    return success_response(
        data={
            "message": "Tenant and user created",
            "user_id": new_user.id,
            "tenant_id": tenant.id,
            "api_key": key,
            "api_key_id": record.id,
        }
    )


@router.post("/resend-verification")
async def resend_verification(
    payload: ResendVerificationRequest, db: AsyncSession = Depends(get_db)
):
    """Resend verification code with cooldown."""
    # Return 404 if email does not exist in either users or signups
    user_exists = await user.get_by_email(db, payload.email)
    signup_exists = await signup.get_by_email(db, payload.email)
    if not user_exists and not signup_exists:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Email does not exist"
        )

    success, remaining = await email_verification_service.resend_code(db, payload.email)
    if not success and remaining is not None:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Please wait {remaining} seconds before requesting another code",
        )
    if not success:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Could not resend code"
        )
    return success_response(data={"message": "Verification code sent"})


@router.post("/refresh", response_model=Token)
async def refresh_access_token(
    request: RefreshTokenRequest, db: AsyncSession = Depends(get_db)
):
    """Refresh access token using refresh token"""
    # Validate refresh token
    if not await refresh_token_service.is_valid(request.refresh_token):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid refresh token"
        )

    # Get user ID from token
    user_id = await refresh_token_service.get_user_id(request.refresh_token)
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid refresh token"
        )

    # Get user
    db_user = await user.get(db, user_id)
    if not db_user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found"
        )

    # Create new access token
    access_token_expires = timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token_data = {
        "user_id": db_user.id,  # Consistent with login endpoint
        "tenant_id": db_user.tenant_id,
        "role": user.get_role_name(db_user),
    }
    access_token = create_access_token(
        data=access_token_data, expires_delta=access_token_expires
    )

    # Rotate refresh token for security
    new_refresh_token = await refresh_token_service.rotate_token(request.refresh_token)
    if not new_refresh_token:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to rotate refresh token",
        )

    return success_response(
        data={
            "access_token": access_token,
            "refresh_token": new_refresh_token,
            "token_type": "bearer",
            "expires_in": settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        }
    )


@router.post("/logout")
async def logout(
    request: RefreshTokenRequest, current_user: User = Depends(get_current_user)
):
    """Logout user by revoking refresh token"""
    success = await refresh_token_service.revoke_token(request.refresh_token)
    if not success:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid refresh token"
        )
    return success_response(data={"message": "Successfully logged out"})


@router.post("/logout-all")
async def logout_all(current_user: User = Depends(get_current_user)):
    """Logout from all devices by revoking all refresh tokens"""
    count = await refresh_token_service.revoke_all_user_tokens(current_user.id)
    return success_response(
        data={"message": f"Successfully logged out from {count} devices"}
    )


@router.get("/me", response_model=User)
async def read_users_me(current_user: User = Depends(get_current_user)):
    """Get current user information"""
    # Populate role and tenant names for response
    current_user.role_name = user.get_role_name(current_user)
    current_user.tenant_name = user.get_tenant_name(current_user)
    return success_response(data=User.model_validate(current_user))


# Forgot Password Flow


@router.post("/forgot-password")
async def forgot_password(
    payload: ForgotPasswordRequest, db: AsyncSession = Depends(get_db)
):
    """Issue a password reset email if user exists (always returns 200)."""
    # Normalize email to lowercase
    email = payload.email.strip().lower()

    # Cooldown to prevent abuse (applies regardless of existence)
    COOLDOWN_SECONDS = 60
    if await redis_manager.is_password_reset_on_cooldown(email):
        remaining = await redis_manager.get_password_reset_cooldown_remaining(email)
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Please wait {remaining} seconds before requesting another reset",
        )

    # Attempt to issue reset; returns True even if user does not exist
    sent = await password_reset_service.issue_reset(db, email)
    if not sent:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to initiate reset",
        )
    # Start cooldown regardless of existence
    await redis_manager.set_password_reset_cooldown(email, COOLDOWN_SECONDS)
    return success_response(
        data={"message": "If the email exists, a reset link has been sent"}
    )


@router.post("/forgot-password/verify")
async def verify_reset_token(payload: VerifyResetTokenRequest):
    """Verify that a reset token is currently valid for the provided email."""
    ok = await password_reset_service.verify_token(
        payload.email.strip().lower(), payload.token
    )
    if not ok:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid or expired token"
        )
    return success_response(data={"message": "Token is valid"})


@router.post("/reset-password")
async def reset_password(
    payload: ResetPasswordRequest, db: AsyncSession = Depends(get_db)
):
    """Consume the token and set the new password if valid."""
    email = payload.email.strip().lower()
    ok = await password_reset_service.consume_and_reset_password(
        db, email, payload.token, payload.new_password
    )
    if not ok:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid or expired token"
        )
    # Revoke all refresh tokens for this user after password change
    db_user = await user.get_by_email(db, email)
    if db_user:
        await refresh_token_service.revoke_all_user_tokens(db_user.id)
    return success_response(data={"message": "Password has been reset successfully"})


@router.post("/status", response_model=AuthStatusResponse)
async def get_auth_status_by_email(
    payload: AuthStatusRequest, db: AsyncSession = Depends(get_db)
):
    """Get the current authentication stage for a user by email."""
    status_data = await auth_status_service.get_status_by_email(db, payload.email)
    return success_response(data=status_data)
