"""
Token tracking service for real-time token usage monitoring.
Cost calculation is done during aggregation to avoid performance overhead.
"""

import logging
from datetime import date

from sqlalchemy.ext.asyncio import AsyncSession

from app.crud.daily_token_usage import daily_token_usage

logger = logging.getLogger(__name__)


class TokenTrackingService:
    """Service for tracking token usage in real-time"""

    async def track_message_tokens(
        self,
        db: AsyncSession,
        tenant_id: int,
        model_name: str,
        input_tokens: int,
        output_tokens: int,
        total_tokens: int,
    ) -> dict:
        """
        Track token usage for a message in real-time.
        Updates daily token usage table (costs calculated during aggregation).

        Args:
            db: Database session
            tenant_id: Tenant ID
            model_name: Model name used
            input_tokens: Number of input tokens
            output_tokens: Number of output tokens
            total_tokens: Total tokens used

        Returns:
            Dictionary with tracking status
        """
        try:
            usage_date = date.today()

            # Update daily token usage (no cost calculation here for performance)
            await daily_token_usage.create_or_update_daily_usage(
                db=db,
                tenant_id=tenant_id,
                model_name=model_name,
                usage_date=usage_date,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                total_tokens=total_tokens,
                message_count=1,
            )

            logger.info(
                f"Tracked tokens for tenant {tenant_id}: "
                f"model={model_name}, tokens={total_tokens}"
            )

            return {
                "tracked": True,
                "tokens": total_tokens,
            }

        except Exception as e:
            logger.error(f"Error tracking message tokens: {e}", exc_info=True)
            # Don't fail the message creation if tracking fails
            return {
                "tracked": False,
                "error": str(e),
            }


# Global token tracking service instance
token_tracking_service = TokenTrackingService()
