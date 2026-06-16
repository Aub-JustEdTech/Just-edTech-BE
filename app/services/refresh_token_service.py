"""
Redis-based refresh token service.
"""

from datetime import timedelta

from app.core.config import settings
from app.db.redis_connector import redis_manager
from app.utils.auth import create_refresh_token


class RefreshTokenService:
    """Service for managing refresh tokens in Redis"""

    async def create_token(self, user_id: int, expires_delta: timedelta = None) -> str:
        """Create new refresh token for user"""
        if expires_delta is None:
            expires_delta = timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS)

        token = create_refresh_token()

        success = await redis_manager.set_refresh_token(
            user_id=user_id, token=token, expires_in=expires_delta
        )

        if not success:
            raise RuntimeError("Failed to store refresh token in Redis")

        return token

    async def get_user_id(self, token: str) -> int | None:
        """Get user ID from refresh token"""
        token_data = await redis_manager.get_refresh_token(token)
        if token_data:
            return token_data.get("user_id")
        return None

    async def is_valid(self, token: str) -> bool:
        """Check if refresh token is valid"""
        return await redis_manager.is_refresh_token_valid(token)

    async def revoke_token(self, token: str) -> bool:
        """Revoke a specific refresh token"""
        return await redis_manager.revoke_refresh_token(token)

    async def revoke_all_user_tokens(self, user_id: int) -> int:
        """Revoke all refresh tokens for a user"""
        return await redis_manager.revoke_all_user_tokens(user_id)

    async def rotate_token(self, old_token: str) -> str | None:
        """Rotate refresh token (revoke old, create new)"""
        # Get user ID from old token
        user_id = await self.get_user_id(old_token)
        if not user_id:
            return None

        # Revoke old token
        await self.revoke_token(old_token)

        # Create new token
        return await self.create_token(user_id)


# Global service instance
refresh_token_service = RefreshTokenService()
