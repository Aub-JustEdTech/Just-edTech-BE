"""
Authentication utilities for JWT token handling and password management.
"""

import secrets
from datetime import datetime, timedelta

from jose import ExpiredSignatureError, JWTError, jwt
from passlib.context import CryptContext

from app.core.config import settings

# Password hashing context
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def create_access_token(data: dict, expires_delta: timedelta | None = None):
    """Create JWT access token with user info, role, and tenant"""
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(
            minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES
        )

    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(
        to_encode, settings.SECRET_KEY, algorithm=settings.ALGORITHM
    )
    return encoded_jwt


def create_refresh_token() -> str:
    """Create a secure refresh token"""
    return secrets.token_urlsafe(32)


class TokenVerificationResult:
    """Result of token verification with error information"""

    def __init__(self, payload: dict | None = None, error: str | None = None):
        self.payload = payload
        self.error = error
        self.is_valid = payload is not None
        self.is_expired = error == "expired"
        self.is_invalid = error == "invalid"


def verify_token(token: str) -> TokenVerificationResult:
    """
    Verify JWT token and return verification result with error details.
    Returns TokenVerificationResult containing:
    - payload: Decoded token payload if valid
    - error: "expired" or "invalid" if token is invalid
    """
    try:
        payload = jwt.decode(
            token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM]
        )
        return TokenVerificationResult(payload=payload)
    except ExpiredSignatureError:
        return TokenVerificationResult(error="expired")
    except JWTError:
        return TokenVerificationResult(error="invalid")


def verify_token_and_get_user_id(
    token: str,
) -> tuple[int | None, TokenVerificationResult]:
    """
    Verify token and extract user ID with detailed error information.
    Returns:
    - tuple[user_id, verification_result]
    - user_id: User ID if token is valid, None otherwise
    - verification_result: TokenVerificationResult with error details
    """
    result = verify_token(token)
    if result.payload:
        # Try both 'user_id' (new format) and 'sub' (JWT standard) for compatibility
        user_id = result.payload.get("user_id") or result.payload.get("sub")
        if user_id is not None:
            try:
                return int(user_id), result
            except (ValueError, TypeError):
                return None, result
    return None, result


def get_password_hash(password: str) -> str:
    """Hash password"""
    return pwd_context.hash(password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify password against hash"""
    return pwd_context.verify(plain_password, hashed_password)
