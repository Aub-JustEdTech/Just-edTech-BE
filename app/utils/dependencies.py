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
from app.crud.user_tenant_access import user_tenant_access
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
    db: AsyncSession = Depends(get_db),
) -> User:
    """Require tenant admin role or higher."""
    from app.services.tenant_access_service import is_privileged_admin

    db_user = await user.get(db, user_id=current_user.id)
    if db_user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"message": "Could not validate credentials", "expired": False},
            headers={"WWW-Authenticate": "Bearer"},
        )

    if not is_privileged_admin(db_user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Tenant admin access required"
        )
    return db_user


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
    - super_admin: bypasses all checks
    - tenant_admin: checked against user_tenant_access join table
    - tenant_user: checked against their single tenant_id column
    If check_admin_only=True, tenant_users are rejected regardless of tenant match.
    """

    async def check_tenant_access(
        tenant_id: int,
        current_user: User = Depends(get_current_user),
        db: AsyncSession = Depends(get_db),
    ) -> User:
        if user.is_super_admin(current_user):
            return current_user

        if user.is_tenant_admin(current_user):
            has = await user_tenant_access.has_access(
                db, user_id=current_user.id, tenant_id=tenant_id
            )
            if not has:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Access denied: tenant not in your access list",
                )
            return current_user

        # tenant_user path
        if check_admin_only:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Tenant admin access required",
            )
        if current_user.tenant_id != tenant_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access denied: Different tenant",
            )
        return current_user

    return check_tenant_access


async def resolve_effective_tenant_id(
    current_user: User,
    tenant_id: int | None,
    db: AsyncSession,
) -> int:
    """Resolve the tenant_id a request should be scoped to, for a JWT user.

    super_admin / tenant_admin: an explicit `tenant_id` query param is
    honored (validated against `user_tenant_access` for tenant_admin;
    super_admin bypasses that check) so they can switch tenants regardless
    of whether they also have their own `tenant_id` set.
    Everyone else (or an admin who omits the query param): falls back to
    their own `tenant_id` column.
    """
    is_super = user.is_super_admin(current_user)
    is_switchable_admin = is_super or user.is_tenant_admin(current_user)

    if tenant_id is not None and is_switchable_admin:
        if is_super:
            return tenant_id

        has = await user_tenant_access.has_access(
            db, user_id=current_user.id, tenant_id=tenant_id
        )
        if not has:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access denied: tenant not in your access list",
            )
        return tenant_id

    if current_user.tenant_id is not None:
        return current_user.tenant_id

    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail="tenant_id query parameter is required for admins with access to multiple tenants",
    )


async def get_effective_tenant_id(
    tenant_id: int | None = Query(
        None, description="Tenant to scope to. Required for admins with access to all tenants."
    ),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> int:
    """FastAPI dependency wrapper around `resolve_effective_tenant_id` for a
    plain JWT-only route (see that function for the resolution rules)."""
    return await resolve_effective_tenant_id(current_user, tenant_id, db)


async def resolve_chat_tenant_id(
    principal: User | ChatConsumer,
    tenant_id: int | None,
    db: AsyncSession,
) -> int:
    """Resolve the tenant_id a chat/RAG request should be scoped to, for
    either principal type returned by `get_current_user_or_chat_consumer` /
    `get_principal_with_api_key`.

    A ChatConsumer always carries a concrete `tenant_id` already. A JWT User
    is resolved via the same rules as `resolve_effective_tenant_id` — without
    this, a cross-tenant super_admin/tenant_admin (whose own `tenant_id`
    column is NULL) would silently scope every chat/RAG call to `tenant_id
    is None` instead of the tenant selected in the UI.
    """
    if isinstance(principal, ChatConsumer):
        return principal.tenant_id
    return await resolve_effective_tenant_id(principal, tenant_id, db)


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
    credentials: HTTPAuthorizationCredentials | None = Depends(optional_security),
    x_chat_consumer_uuid: str | None = Header(None, alias="X-Chat-Consumer-UUID"),
    chat_consumer_uuid: str | None = Query(None),
    x_api_key: str | None = Header(None),
    db: AsyncSession = Depends(get_db),
) -> User | ChatConsumer:
    """
    Authenticate either:
      - a JWT tenant user (super_admin, tenant_admin, or tenant_user) — no API
        key required, since their own tenant_id already scopes the request
      - an anonymous chat consumer — an API key is still mandatory here, since
        a bare chat-consumer UUID alone is not proof of tenant authorization

    Returns the authenticated principal (User or ChatConsumer).
    """
    uuid_str = x_chat_consumer_uuid or chat_consumer_uuid

    if not uuid_str:
        # No consumer UUID presented -> this must be a JWT tenant user.
        if not credentials:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Authentication required: provide Bearer token or chat UUID",
            )
        return await _user_from_token(credentials.credentials, db)

    # Chat consumer path — API key remains mandatory, exactly as before.
    if not x_api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing API key",
        )
    api_key_record = await api_keys.get_by_key(db, x_api_key)
    if not api_key_record:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid API key"
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
    if db_chat_consumer.tenant_id != api_key_record.tenant_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="API key tenant mismatch",
        )
    return db_chat_consumer


# Convenience alias for endpoints: JWT tenant user, or API-key-verified chat consumer
require_api_key_user_or_chat_consumer = Depends(get_principal_with_api_key)
