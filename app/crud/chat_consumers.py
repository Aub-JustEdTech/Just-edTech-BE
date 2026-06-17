"""
CRUD operations for ChatConsumer model.
"""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.chat_consumers import ChatConsumer
from app.schemas.chat_consumers import ChatConsumerCreate


class ChatConsumerCRUD:
    """CRUD operations for ChatConsumer model"""

    async def create(
        self, db: AsyncSession, chat_consumer_create: ChatConsumerCreate
    ) -> ChatConsumer:
        """Create new chat consumer with auto-generated UUID"""
        db_chat_consumer = ChatConsumer(tenant_id=chat_consumer_create.tenant_id)
        db.add(db_chat_consumer)
        await db.commit()
        await db.refresh(db_chat_consumer)
        return db_chat_consumer

    async def get_by_uuid(
        self, db: AsyncSession, chat_consumer_uuid: UUID
    ) -> ChatConsumer | None:
        """Get chat consumer by UUID"""
        result = await db.execute(
            select(ChatConsumer).where(
                ChatConsumer.chat_consumer_uuid == chat_consumer_uuid
            )
        )
        return result.scalar_one_or_none()


chat_consumer = ChatConsumerCRUD()
