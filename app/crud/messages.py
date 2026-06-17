"""
CRUD operations for Message model.
"""

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from app.models.conversations import Message


class MessageCRUD:
    """CRUD operations for Message model"""

    async def create_message(
        self,
        db: AsyncSession,
        conversation_id: int,
        role: str,
        content: str,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        total_tokens: int | None = None,
        model_used: str | None = None,
        images: list[dict] | None = None,
    ) -> Message:
        """Create new message with token tracking and images"""
        # For assistant messages (LLM responses), token tracking is mandatory
        if role == "assistant":
            if total_tokens is None:
                total_tokens = 0
            if input_tokens is None:
                input_tokens = 0
            if output_tokens is None:
                output_tokens = 0
            if model_used is None:
                model_used = "unknown"

        db_message = Message(
            conversation_id=conversation_id,
            role=role,
            content=content,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=total_tokens,
            model_used=model_used,
            images=images,
        )
        db.add(db_message)
        await db.commit()
        await db.refresh(db_message)
        return db_message

    async def get_conversation_messages(
        self, db: AsyncSession, conversation_id: int, page: int = 1, per_page: int = 50
    ) -> tuple[list[Message], int]:
        """Get paginated message history with citations"""
        offset = (page - 1) * per_page

        # Get total count
        total_query = select(func.count(Message.id)).where(
            Message.conversation_id == conversation_id
        )
        total_result = await db.execute(total_query)
        total = total_result.scalar()

        # Get messages with citations
        messages_query = (
            select(Message)
            .options(joinedload(Message.citations))
            .where(Message.conversation_id == conversation_id)
            .order_by(Message.created_at.desc())
            .offset(offset)
            .limit(per_page)
        )

        messages_result = await db.execute(messages_query)
        messages = messages_result.unique().scalars().all()
        # Return in chronological order (oldest first) for UI rendering
        return list(reversed(messages)), total

    async def get_recent_messages_for_context(
        self, db: AsyncSession, conversation_id: int, limit: int = 10
    ) -> list[Message]:
        """Get last N messages for bot context"""
        messages_query = (
            select(Message)
            .where(Message.conversation_id == conversation_id)
            .order_by(Message.created_at.desc())
            .limit(limit)
        )

        messages_result = await db.execute(messages_query)
        messages = messages_result.scalars().all()
        # Return in chronological order (oldest first)
        return list(reversed(messages))

    async def get_messages_by_ids(
        self, db: AsyncSession, message_ids: list[int]
    ) -> list[Message]:
        """Get specific messages by their IDs with citations"""
        if not message_ids:
            return []

        messages_query = (
            select(Message)
            .options(joinedload(Message.citations))
            .where(Message.id.in_(message_ids))
        )

        messages_result = await db.execute(messages_query)
        messages = messages_result.unique().scalars().all()
        return messages


message = MessageCRUD()
