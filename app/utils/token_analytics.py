"""
Token analytics utilities for tracking LLM usage costs.
"""

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.conversations import Conversation, Message


class TokenAnalytics:
    """Utility class for token usage analytics"""

    async def get_user_token_usage(
        self, db: AsyncSession, user_id: int, tenant_id: int | None = None
    ) -> dict:
        """
        Get token usage statistics for a specific user.

        Args:
            db: Database session
            user_id: User ID to analyze
            tenant_id: Optional tenant ID to filter by

        Returns:
            Dictionary with token usage statistics
        """
        # Build query for user's conversations
        query = select(Conversation.id).where(Conversation.user_id == user_id)

        if tenant_id:
            query = query.where(Conversation.tenant_id == tenant_id)

        # Get conversation IDs
        result = await db.execute(query)
        conversation_ids = [row[0] for row in result.all()]

        if not conversation_ids:
            return {
                "user_id": user_id,
                "tenant_id": tenant_id,
                "total_messages": 0,
                "total_input_tokens": 0,
                "total_output_tokens": 0,
                "total_tokens": 0,
                "unique_models": [],
                "average_tokens_per_message": 0,
            }

        # Get token usage statistics
        stats_query = select(
            func.count(Message.id).label("total_messages"),
            func.coalesce(func.sum(Message.input_tokens), 0).label(
                "total_input_tokens"
            ),
            func.coalesce(func.sum(Message.output_tokens), 0).label(
                "total_output_tokens"
            ),
            func.coalesce(func.sum(Message.total_tokens), 0).label("total_tokens"),
            func.array_agg(func.distinct(Message.model_used)).label("unique_models"),
        ).where(
            Message.conversation_id.in_(conversation_ids),
            Message.role
            == "assistant",  # Only count assistant messages (LLM responses)
        )

        stats_result = await db.execute(stats_query)
        stats = stats_result.first()

        # Calculate average tokens per message
        avg_tokens = 0
        if stats.total_messages > 0:
            avg_tokens = stats.total_tokens / stats.total_messages

        return {
            "user_id": user_id,
            "tenant_id": tenant_id,
            "total_messages": stats.total_messages,
            "total_input_tokens": stats.total_input_tokens,
            "total_output_tokens": stats.total_output_tokens,
            "total_tokens": stats.total_tokens,
            "unique_models": [model for model in stats.unique_models if model],
            "average_tokens_per_message": round(avg_tokens, 2),
        }

    async def get_tenant_token_usage(self, db: AsyncSession, tenant_id: int) -> dict:
        """
        Get token usage statistics for a specific tenant.

        Args:
            db: Database session
            tenant_id: Tenant ID to analyze

        Returns:
            Dictionary with tenant token usage statistics
        """
        # Get all conversation IDs for the tenant
        conversation_query = select(Conversation.id).where(
            Conversation.tenant_id == tenant_id
        )

        result = await db.execute(conversation_query)
        conversation_ids = [row[0] for row in result.all()]

        if not conversation_ids:
            return {
                "tenant_id": tenant_id,
                "total_messages": 0,
                "total_input_tokens": 0,
                "total_output_tokens": 0,
                "total_tokens": 0,
                "unique_models": [],
                "average_tokens_per_message": 0,
                "unique_users": 0,
            }

        # Get token usage statistics
        stats_query = (
            select(
                func.count(Message.id).label("total_messages"),
                func.coalesce(func.sum(Message.input_tokens), 0).label(
                    "total_input_tokens"
                ),
                func.coalesce(func.sum(Message.output_tokens), 0).label(
                    "total_output_tokens"
                ),
                func.coalesce(func.sum(Message.total_tokens), 0).label("total_tokens"),
                func.array_agg(func.distinct(Message.model_used)).label(
                    "unique_models"
                ),
                func.count(func.distinct(Conversation.user_id)).label("unique_users"),
            )
            .select_from(Message)
            .join(Conversation, Message.conversation_id == Conversation.id)
            .where(
                Message.conversation_id.in_(conversation_ids),
                Message.role
                == "assistant",  # Only count assistant messages (LLM responses)
            )
        )

        stats_result = await db.execute(stats_query)
        stats = stats_result.first()

        # Calculate average tokens per message
        avg_tokens = 0
        if stats.total_messages > 0:
            avg_tokens = stats.total_tokens / stats.total_messages

        return {
            "tenant_id": tenant_id,
            "total_messages": stats.total_messages,
            "total_input_tokens": stats.total_input_tokens,
            "total_output_tokens": stats.total_output_tokens,
            "total_tokens": stats.total_tokens,
            "unique_models": [model for model in stats.unique_models if model],
            "average_tokens_per_message": round(avg_tokens, 2),
            "unique_users": stats.unique_users,
        }

    async def get_token_usage_by_model(
        self, db: AsyncSession, tenant_id: int | None = None
    ) -> list[dict]:
        """
        Get token usage breakdown by model.

        Args:
            db: Database session
            tenant_id: Optional tenant ID to filter by

        Returns:
            List of dictionaries with model usage statistics
        """
        # Build base query
        query = (
            select(
                Message.model_used,
                func.count(Message.id).label("message_count"),
                func.coalesce(func.sum(Message.input_tokens), 0).label(
                    "total_input_tokens"
                ),
                func.coalesce(func.sum(Message.output_tokens), 0).label(
                    "total_output_tokens"
                ),
                func.coalesce(func.sum(Message.total_tokens), 0).label("total_tokens"),
            )
            .where(Message.role == "assistant")
            .group_by(Message.model_used)
        )

        if tenant_id:
            query = query.join(Conversation).where(Conversation.tenant_id == tenant_id)

        result = await db.execute(query)
        return [
            {
                "model": row.model_used,
                "message_count": row.message_count,
                "total_input_tokens": row.total_input_tokens,
                "total_output_tokens": row.total_output_tokens,
                "total_tokens": row.total_tokens,
            }
            for row in result.all()
        ]


# Global token analytics instance
token_analytics = TokenAnalytics()
