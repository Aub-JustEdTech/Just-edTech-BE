"""
User schemas for request/response validation.
"""

import re
from enum import Enum

from pydantic import BaseModel, EmailStr, validator


class UserBase(BaseModel):
    """Base user schema"""

    email: EmailStr
    name: str | None = None


class UserCreate(UserBase):
    """Schema for user creation"""

    name: str
    password: str
    email: EmailStr
    tenant_id: int | None = None
    role_id: int | None = None

    @validator("password")
    def validate_password(cls, v):
        """Validate password strength"""
        if len(v) < 8:
            raise ValueError("Password must be at least 8 characters long")
        if not re.search(r"[A-Z]", v):
            raise ValueError("Password must contain at least one uppercase letter")
        if not re.search(r"[a-z]", v):
            raise ValueError("Password must contain at least one lowercase letter")
        if not re.search(r"\d", v):
            raise ValueError("Password must contain at least one digit")
        return v


class UserUpdate(BaseModel):
    """Schema for user updates"""

    email: EmailStr | None = None
    name: str | None = None
    role_id: int | None = None


class UserInDB(UserBase):
    """Schema for user in database"""

    id: int
    tenant_id: int
    password_hash: str
    role_id: int | None = None

    class Config:
        from_attributes = True


class User(UserBase):
    """Schema for user response"""

    id: int
    tenant_id: int
    role_id: int | None = None
    role_name: str | None = None  # Populated from relationship
    tenant_name: str | None = None  # Populated from relationship

    class Config:
        from_attributes = True


class Token(BaseModel):
    """Token schema"""

    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int


class LoginRequest(BaseModel):
    """Login request payload with JSON body"""

    username: str
    password: str


class RefreshTokenRequest(BaseModel):
    """Refresh token request schema"""

    refresh_token: str


class TokenData(BaseModel):
    """Token data schema"""

    user_id: int | None = None
    tenant_id: int | None = None
    role: str | None = None


class VerifyEmailRequest(BaseModel):
    """Verify email with a code"""

    email: EmailStr
    code: str


class ResendVerificationRequest(BaseModel):
    """Request new verification code"""

    email: EmailStr


class SignupRequest(BaseModel):
    """Create signup before tenant/user are created"""

    email: EmailStr
    name: str
    password: str
    invite_token: str | None = None


class InvitationCreateRequest(BaseModel):
    email: EmailStr
    role_id: int | None = None


class BulkInvitationCreateRequest(BaseModel):
    emails: list[EmailStr]
    role_id: int | None = None

    @validator("emails")
    def validate_emails(cls, v):
        if len(v) == 0:
            raise ValueError("At least one email is required")
        if len(v) > 9:
            raise ValueError("Maximum 9 emails allowed per request")
        # Check for duplicates (case-insensitive)
        normalized = [e.lower().strip() for e in v]
        if len(normalized) != len(set(normalized)):
            raise ValueError("Duplicate emails are not allowed")
        return v


class InvitationValidateResponse(BaseModel):
    email: EmailStr
    tenant_id: int
    role_id: int | None = None


class BulkInvitationResponse(BaseModel):
    total: int
    successful: int
    failed: int
    results: list[dict[str, str | bool]]


class SetupTenantRequest(BaseModel):
    """After OTP, supply tenant details to create tenant and user"""

    email: EmailStr
    tenant_name: str
    tenant_domain: str | None = None
    logo_b64: str | None = None


class ForgotPasswordRequest(BaseModel):
    """Initiate forgot password flow"""

    email: EmailStr


class VerifyResetTokenRequest(BaseModel):
    """Verify reset token validity"""

    email: EmailStr
    token: str


class ResetPasswordRequest(BaseModel):
    """Reset password with a valid token"""

    email: EmailStr
    token: str
    new_password: str
    confirm_password: str

    @validator("new_password")
    def validate_new_password(cls, v):
        if len(v) < 8:
            raise ValueError("Password must be at least 8 characters long")
        if not re.search(r"[A-Z]", v):
            raise ValueError("Password must contain at least one uppercase letter")
        if not re.search(r"[a-z]", v):
            raise ValueError("Password must contain at least one lowercase letter")
        if not re.search(r"\d", v):
            raise ValueError("Password must contain at least one digit")
        return v

    @validator("confirm_password")
    def confirm_matches(cls, v, values):
        if "new_password" in values and v != values["new_password"]:
            raise ValueError("Passwords do not match")
        return v


class AuthStage(str, Enum):
    """Authentication stage enum"""

    NOT_SIGNED_UP = "not_signed_up"
    SIGNED_UP_PENDING_VERIFICATION = "signed_up_pending_verification"
    EMAIL_VERIFIED_PENDING_TENANT_SETUP = "email_verified_pending_tenant_setup"
    INVITED_PENDING_REGISTRATION = "invited_pending_registration"
    USER_EXISTS_UNVERIFIED = "user_exists_unverified"
    FULLY_ACTIVE = "fully_active"


class AuthStatusRequest(BaseModel):
    """Request schema for authentication status check"""

    email: EmailStr


class AuthStatusResponse(BaseModel):
    """Response schema for authentication status"""

    stage: AuthStage
    email: str
    message: str
    next_action: str | None = None
    details: dict | None = None
