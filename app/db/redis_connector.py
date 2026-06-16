"""
Redis connection management for session storage and caching.
"""

import json
from datetime import timedelta
from typing import Any

import redis.asyncio as redis
from redis.asyncio import Redis

from app.core.config import settings


class RedisManager:
    """Redis connection and operations manager"""

    def __init__(self):
        self._redis: Redis | None = None

    async def connect(self):
        """Initialize Redis connection"""
        if not self._redis:
            self._redis = redis.from_url(
                settings.REDIS_URL,
                encoding="utf-8",
                decode_responses=True,
                socket_connect_timeout=5,
                socket_timeout=5,
                retry_on_timeout=True,
                health_check_interval=30,
            )
        return self._redis

    async def disconnect(self):
        """Close Redis connection"""
        if self._redis:
            await self._redis.close()
            self._redis = None

    async def get_redis(self) -> Redis:
        """Get Redis connection"""
        if not self._redis:
            await self.connect()
        return self._redis

    # Refresh Token Operations
    async def set_refresh_token(
        self, user_id: int, token: str, expires_in: timedelta = None
    ) -> bool:
        """Store refresh token in Redis with expiration"""
        redis = await self.get_redis()

        if expires_in is None:
            expires_in = timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS)

        key = f"refresh_token:{token}"
        from datetime import datetime

        value = {"user_id": user_id, "created_at": datetime.utcnow().isoformat()}

        # Store token with automatic expiration
        result = await redis.setex(
            key, int(expires_in.total_seconds()), json.dumps(value)
        )

        # Also maintain a user -> tokens mapping for logout-all functionality
        user_tokens_key = f"user_tokens:{user_id}"
        await redis.sadd(user_tokens_key, token)
        await redis.expire(user_tokens_key, int(expires_in.total_seconds()))

        return result

    async def get_refresh_token(self, token: str) -> dict | None:
        """Get refresh token data"""
        redis = await self.get_redis()
        key = f"refresh_token:{token}"

        data = await redis.get(key)
        if data:
            return json.loads(data)
        return None

    async def is_refresh_token_valid(self, token: str) -> bool:
        """Check if refresh token exists and is valid"""
        data = await self.get_refresh_token(token)
        return data is not None

    async def revoke_refresh_token(self, token: str) -> bool:
        """Revoke a specific refresh token"""
        redis = await self.get_redis()

        # Get token data first to get user_id
        token_data = await self.get_refresh_token(token)
        if not token_data:
            return False

        user_id = token_data["user_id"]

        # Remove from token storage
        key = f"refresh_token:{token}"
        result = await redis.delete(key)

        # Remove from user tokens set
        user_tokens_key = f"user_tokens:{user_id}"
        await redis.srem(user_tokens_key, token)

        return bool(result)

    async def revoke_all_user_tokens(self, user_id: int) -> int:
        """Revoke all refresh tokens for a user"""
        redis = await self.get_redis()
        user_tokens_key = f"user_tokens:{user_id}"

        # Get all tokens for this user
        tokens = await redis.smembers(user_tokens_key)

        if not tokens:
            return 0

        # Delete all token keys
        token_keys = [f"refresh_token:{token}" for token in tokens]
        deleted_count = await redis.delete(*token_keys)

        # Clear the user tokens set
        await redis.delete(user_tokens_key)

        return deleted_count

    async def cleanup_expired_tokens(self) -> int:
        """Redis automatically handles TTL, but this can be used for manual cleanup if needed"""
        # Redis automatically expires keys with TTL, so this is mainly for monitoring
        redis = await self.get_redis()

        # Get info about expired keys (this is more for logging/monitoring)
        await redis.info("keyspace")
        return 0  # Redis handles cleanup automatically

    # Password Reset Token Operations
    async def set_password_reset_token(
        self, email: str, token: str, expires_in_seconds: int
    ) -> bool:
        """Store a short-lived secure token against an email for password reset."""
        redis = await self.get_redis()
        key = f"password_reset:{email}"
        return await redis.setex(key, expires_in_seconds, token)

    async def get_password_reset_token(self, email: str) -> str | None:
        """Get the reset token stored for an email (if any)."""
        redis = await self.get_redis()
        key = f"password_reset:{email}"
        return await redis.get(key)

    async def consume_password_reset_token(self, email: str, token: str) -> bool:
        """Validate and remove the reset token in a race-safe manner."""
        redis = await self.get_redis()
        key = f"password_reset:{email}"
        pipe = redis.pipeline()
        pipe.get(key)
        pipe.delete(key)
        stored_token, _ = await pipe.execute()
        return stored_token is not None and stored_token == token

    # Password Reset Cooldown Operations
    async def set_password_reset_cooldown(self, email: str, seconds: int) -> bool:
        """Start a cooldown for password reset requests for the given email."""
        redis = await self.get_redis()
        key = f"password_reset_cooldown:{email}"
        return await redis.setex(key, seconds, "1")

    async def is_password_reset_on_cooldown(self, email: str) -> bool:
        """Check if password reset resend is on cooldown for the email."""
        redis = await self.get_redis()
        key = f"password_reset_cooldown:{email}"
        exists = await redis.exists(key)
        return bool(exists)

    async def get_password_reset_cooldown_remaining(self, email: str) -> int:
        """Return remaining cooldown seconds (or -1 if none)."""
        redis = await self.get_redis()
        key = f"password_reset_cooldown:{email}"
        ttl = await redis.ttl(key)
        return int(ttl) if ttl is not None else -1

    # Invitation TTL and Cooldown Operations
    async def set_invite_token_ttl(self, token: str, seconds: int) -> bool:
        """Store an invite token presence with TTL to enforce expiration."""
        redis = await self.get_redis()
        key = f"invite_token:{token}"
        return await redis.setex(key, seconds, "1")

    async def exists_invite_token(self, token: str) -> bool:
        redis = await self.get_redis()
        key = f"invite_token:{token}"
        exists = await redis.exists(key)
        return bool(exists)

    async def set_invite_cooldown(self, email: str, seconds: int) -> bool:
        redis = await self.get_redis()
        key = f"invite_cooldown:{email}"
        return await redis.setex(key, seconds, "1")

    async def is_invite_on_cooldown(self, email: str) -> bool:
        redis = await self.get_redis()
        key = f"invite_cooldown:{email}"
        exists = await redis.exists(key)
        return bool(exists)

    async def get_invite_cooldown_remaining(self, email: str) -> int:
        redis = await self.get_redis()
        key = f"invite_cooldown:{email}"
        ttl = await redis.ttl(key)
        return int(ttl) if ttl is not None else -1

    # Email Verification Code (OTP) Operations
    async def set_email_verification_code(
        self, email: str, code: str, expires_in_seconds: int
    ) -> bool:
        """Store a short-lived numeric verification code against an email."""
        redis = await self.get_redis()
        key = f"email_verify_code:{email}"
        return await redis.setex(key, expires_in_seconds, code)

    async def get_email_verification_code(self, email: str) -> str | None:
        """Get the code stored for an email (if any)."""
        redis = await self.get_redis()
        key = f"email_verify_code:{email}"
        return await redis.get(key)

    async def consume_email_verification_code(self, email: str, code: str) -> bool:
        """Validate and remove the code in a race-safe manner."""
        redis = await self.get_redis()
        key = f"email_verify_code:{email}"
        pipe = redis.pipeline()
        pipe.get(key)
        pipe.delete(key)
        stored_code, _ = await pipe.execute()
        return stored_code is not None and stored_code == code

    # Resend Cooldown Operations
    async def set_resend_cooldown(self, email: str, seconds: int) -> bool:
        """Start a resend cooldown for the given email."""
        redis = await self.get_redis()
        key = f"email_verify_cooldown:{email}"
        return await redis.setex(key, seconds, "1")

    async def is_resend_on_cooldown(self, email: str) -> bool:
        """Check if resend is currently on cooldown for the email."""
        redis = await self.get_redis()
        key = f"email_verify_cooldown:{email}"
        exists = await redis.exists(key)
        return bool(exists)

    async def get_resend_cooldown_remaining(self, email: str) -> int:
        """Return remaining cooldown seconds (or -1 if none)."""
        redis = await self.get_redis()
        key = f"email_verify_cooldown:{email}"
        ttl = await redis.ttl(key)
        return int(ttl) if ttl is not None else -1

    # Verified-pending flag after OTP success
    async def set_verified_pending(self, email: str, seconds: int) -> bool:
        """Mark email as OTP-verified, pending tenant setup."""
        redis = await self.get_redis()
        key = f"email_verified_pending:{email}"
        return await redis.setex(key, seconds, "1")

    async def is_verified_pending(self, email: str) -> bool:
        redis = await self.get_redis()
        key = f"email_verified_pending:{email}"
        exists = await redis.exists(key)
        return bool(exists)

    async def consume_verified_pending(self, email: str) -> bool:
        redis = await self.get_redis()
        key = f"email_verified_pending:{email}"
        res = await redis.delete(key)
        return bool(res)

    # General Redis Operations
    async def set(self, key: str, value: Any, expire: timedelta = None) -> bool:
        """Set a key-value pair with optional expiration"""
        redis = await self.get_redis()

        if isinstance(value, dict | list):
            value = json.dumps(value)

        if expire:
            return await redis.setex(key, int(expire.total_seconds()), value)
        else:
            return await redis.set(key, value)

    async def get(self, key: str) -> str | None:
        """Get value by key"""
        redis = await self.get_redis()
        return await redis.get(key)

    async def delete(self, key: str) -> bool:
        """Delete a key"""
        redis = await self.get_redis()
        result = await redis.delete(key)
        return bool(result)

    async def exists(self, key: str) -> bool:
        """Check if key exists"""
        redis = await self.get_redis()
        result = await redis.exists(key)
        return bool(result)

    async def ping(self) -> bool:
        """Test Redis connection"""
        try:
            redis = await self.get_redis()
            result = await redis.ping()
            return result
        except Exception:
            return False


# Global Redis manager instance
redis_manager = RedisManager()


async def get_redis() -> Redis:
    """Dependency to get Redis connection"""
    return await redis_manager.get_redis()


# Startup and shutdown events
async def init_redis():
    """Initialize Redis connection on startup"""
    await redis_manager.connect()
    print("✅ Redis connected successfully")


async def close_redis():
    """Close Redis connection on shutdown"""
    await redis_manager.disconnect()
    print("✅ Redis connection closed")
