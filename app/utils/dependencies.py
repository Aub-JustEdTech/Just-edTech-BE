"""
FastAPI dependencies for authentication and database session management.
"""

from collections.abc import Callable
from uuid import UUID

from fastapi import Depends, Header, HTTPException, Query, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.crud.api_keys import api_keys
from app.crud.chat_consumers import chat_consumer
from app.crud.users import user
from app.db.connector import get_session
from app.models.chat_consumers import ChatConsumer
from app.models.users import User
from app.utils.auth import verify_token_and_get_user_id

security = HTTPBearer()
optional_security = HTTPBearer(auto_error=False)


async def get_db() -> AsyncSession:
    """Dependency to get database session"""
    async for session in get_session():
        yield session


async def _user_from_token(
    token: str,
    db: AsyncSession,
) -> User:
    """Validate a JWT and return the corresponding User, raising HTTPException on failure."""
    user_id, verification_result = verify_token_and_get_user_id(token)
    if verification_result.is_expired:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"message": "Token expired", "expired": True},
            headers={"WWW-Authenticate": "Bearer"},
        )
    if verification_result.is_invalid or user_id is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"message": "Invalid token", "expired": False},
            headers={"WWW-Authenticate": "Bearer"},
        )
    db_user = await user.get(db, user_id=user_id)
    if db_user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"message": "Could not validate credentials", "expired": False},
            headers={"WWW-Authenticate": "Bearer"},
        )
    return db_user


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: AsyncSession = Depends(get_db),
) -> User:
    """Get current authenticated user with specific error handling for expired/invalid tokens"""
    return await _user_from_token(credentials.credentials, db)


# Role-based access control dependencies


async def get_current_super_admin(
    current_user: User = Depends(get_current_user),
) -> User:
    """Require super admin role"""
    if not user.is_super_admin(current_user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Super admin access required"
        )
    return current_user


async def get_current_tenant_admin(
    current_user: User = Depends(get_current_user),
) -> User:
    """Require tenant admin role or higher"""
    if not (user.is_super_admin(current_user) or user.is_tenant_admin(current_user)):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Tenant admin access required"
        )
    return current_user


async def get_current_tenant_user(
    current_user: User = Depends(get_current_user),
) -> User:
    """Require any tenant user role (includes all roles)"""
    if not current_user.role:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="User role required"
        )
    return current_user


def require_tenant_access(check_admin_only: bool = False):
    """
    Factory function to create tenant-specific access dependency.
    If check_admin_only=True, only tenant admins and super admins can access.
    If check_admin_only=False, any user in the same tenant can access.
    """

    async def check_tenant_access(
        tenant_id: int,
        current_user: User = Depends(get_current_user),
    ) -> User:
        # Super admins have access to all tenants
        if user.is_super_admin(current_user):
            return current_user

        # Check if user belongs to the requested tenant
        if current_user.tenant_id != tenant_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access denied: Different tenant",
            )

        # If admin-only check is required
        if check_admin_only and not user.is_tenant_admin(current_user):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Tenant admin access required",
            )

        return current_user

    return check_tenant_access


def require_role(*allowed_roles: str) -> Callable:
    """
    Factory function to create role-specific access dependency.
    Usage: require_role("super_admin", "tenant_admin")
    """

    async def check_role(
        current_user: User = Depends(get_current_user),
    ) -> User:
        if not current_user.role:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail="User role required"
            )

        user_role = current_user.role.name
        if user_role not in allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Role access denied. Required: {', '.join(allowed_roles)}",
            )

        return current_user

    return check_role


# Chat Consumer Dependencies


async def get_chat_consumer_from_uuid(
    x_chat_consumer_uuid: str | None = Header(None, alias="X-Chat-Consumer-UUID"),
    chat_consumer_uuid: str | None = Query(None),
    db: AsyncSession = Depends(get_db),
) -> ChatConsumer:
    """Get chat consumer from UUID header or query parameter"""
    uuid_str = x_chat_consumer_uuid or chat_consumer_uuid

    if not uuid_str:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Chat consumer UUID required. Provide via X-Chat-Consumer-UUID header or chat_consumer_uuid query parameter",
        )

    try:
        consumer_uuid = UUID(uuid_str)
    except ValueError as err:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid UUID format",
        ) from err

    db_chat_consumer = await chat_consumer.get_by_uuid(db, consumer_uuid)
    if not db_chat_consumer:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid chat consumer UUID",
        )

    return db_chat_consumer


# Hybrid authentication that supports both users and chat consumers
async def get_current_user_or_chat_consumer(
    credentials: HTTPAuthorizationCredentials | None = Depends(optional_security),
    x_chat_consumer_uuid: str | None = Header(None, alias="X-Chat-Consumer-UUID"),
    chat_consumer_uuid: str | None = Query(None),
    db: AsyncSession = Depends(get_db),
) -> User | ChatConsumer:
    """
    Hybrid authentication that supports both User and ChatConsumer authentication.
    Tries chat consumer authentication first (via header/query), then falls back to user authentication.
    """
    # Try chat consumer authentication first
    uuid_str = x_chat_consumer_uuid or chat_consumer_uuid
    if uuid_str:
        try:
            consumer_uuid = UUID(uuid_str)
            db_chat_consumer = await chat_consumer.get_by_uuid(db, consumer_uuid)
            if db_chat_consumer:
                return db_chat_consumer
        except ValueError:
            # Invalid UUID format, continue to user auth
            pass

    # Fall back to user authentication if no chat consumer UUID or invalid UUID
    if not credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required. Provide either X-Chat-Consumer-UUID header/query parameter or Bearer token",
        )
    return await _user_from_token(credentials.credentials, db)


# Convenience aliases for common role combinations
require_super_admin = Depends(get_current_super_admin)
require_tenant_admin = Depends(get_current_tenant_admin)
require_chat_consumer = Depends(get_chat_consumer_from_uuid)
require_user_or_chat_consumer = Depends(get_current_user_or_chat_consumer)


async def require_api_key(
    db: AsyncSession = Depends(get_db), x_api_key: str | None = Header(None)
):
    """Authenticate requests by X-API-Key header (single key).

    Returns a dict with tenant_id for downstream use.
    """
    if not x_api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing API key",
        )

    # Use the provided key directly to look up the record
    record = await api_keys.get_by_key(db, x_api_key)
    if not record:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid API key"
        )

    return {"tenant_id": record.tenant_id, "api_key_id": record.id}


async def get_principal_with_api_key(
    api_key_info: dict = Depends(require_api_key),
    credentials: HTTPAuthorizationCredentials | None = Depends(optional_security),
    x_chat_consumer_uuid: str | None = Header(None, alias="X-Chat-Consumer-UUID"),
    chat_consumer_uuid: str | None = Query(None),
    db: AsyncSession = Depends(get_db),
) -> User | ChatConsumer:
    """
    Require a valid API key AND one of:
      - a valid JWT user
      - a valid chat consumer UUID (via header or query)

    Ensures the tenant from API key matches the principal tenant.
    Returns the authenticated principal (User or ChatConsumer).
    """
    # Try chat consumer auth first if UUID provided
    uuid_str = x_chat_consumer_uuid or chat_consumer_uuid
    if uuid_str:
        try:
            consumer_uuid = UUID(uuid_str)
        except ValueError as err:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid UUID format",
            ) from err
        db_chat_consumer = await chat_consumer.get_by_uuid(db, consumer_uuid)
        if not db_chat_consumer:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid chat consumer UUID",
            )
        if db_chat_consumer.tenant_id != api_key_info["tenant_id"]:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="API key tenant mismatch",
            )
        return db_chat_consumer

    # Else require JWT user
    if not credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required: provide Bearer token or chat UUID",
        )
    db_user = await _user_from_token(credentials.credentials, db)
    if db_user.tenant_id != api_key_info["tenant_id"]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="API key tenant mismatch",
        )
    return db_user


# Convenience alias for endpoints: require API key + (user or chat consumer)
require_api_key_user_or_chat_consumer = Depends(get_principal_with_api_key)
