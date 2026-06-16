"""
CRUD operations for Conversation model.
"""

from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from app.models.conversations import Conversation, Message
from app.schemas.conversations import ConversationListItem


class ConversationCRUD:
    """CRUD operations for Conversation model"""

    async def get(self, db: AsyncSession, conversation_id: int) -> Conversation | None:
        """Get conversation by ID with relationships loaded"""
        result = await db.execute(
            select(Conversation)
            .options(joinedload(Conversation.chat_consumer))
            .where(Conversation.id == conversation_id)
        )
        return result.scalar_one_or_none()

    async def get_conversation_by_id(
        self, db: AsyncSession, conversation_id: int
    ) -> Conversation | None:
        """Get conversation by ID with messages and citations loaded"""
        result = await db.execute(
            select(Conversation)
            .options(
                joinedload(Conversation.messages).joinedload(Message.citations)
            )
            .where(Conversation.id == conversation_id)
        )
        return result.unique().scalar_one_or_none()

    async def get_conversations(
        self,
        db: AsyncSession,
        tenant_id: int,
        page: int = 1,
        per_page: int = 20,
        user_id: int | None = None,
        chat_consumer_id: int | None = None,
    ) -> tuple[list[ConversationListItem], int]:
        """Get conversations with pagination and last message preview"""
        offset = (page - 1) * per_page

        # Build where conditions
        where_conditions = [Conversation.tenant_id == tenant_id]
        if user_id is not None:
            where_conditions.append(Conversation.user_id == user_id)
        if chat_consumer_id is not None:
            where_conditions.append(Conversation.chat_consumer_id == chat_consumer_id)

        # Get total count
        total_query = select(func.count(Conversation.id)).where(*where_conditions)
        total_result = await db.execute(total_query)
        total = total_result.scalar()

        # Get conversations with last message
        conversations_query = (
            select(Conversation)
            .where(*where_conditions)
            .order_by(Conversation.updated_at.desc())
            .offset(offset)
            .limit(per_page)
        )

        conversations_result = await db.execute(conversations_query)
        conversations = conversations_result.scalars().all()

        # Batch-fetch last message preview for all conversations in one query
        last_msg_map: dict[int, str | None] = {}
        if conversations:
            conv_ids = [c.id for c in conversations]
            row_num = func.row_number().over(
                partition_by=Message.conversation_id,
                order_by=Message.created_at.desc(),
            ).label("rn")
            subq = (
                select(Message.conversation_id, Message.content, row_num)
                .where(Message.conversation_id.in_(conv_ids))
                .subquery()
            )
            last_msgs_result = await db.execute(
                select(subq.c.conversation_id, subq.c.content).where(subq.c.rn == 1)
            )
            last_msg_map = {
                row.conversation_id: row.content for row in last_msgs_result
            }

        items = []
        for conv in conversations:
            last_message = last_msg_map.get(conv.id)
            preview = None
            if last_message:
                preview = (
                    last_message[:100] + "..."
                    if len(last_message) > 100
                    else last_message
                )

            items.append(
                ConversationListItem(
                    id=conv.id,
                    title=conv.title,
                    created_at=conv.created_at,
                    updated_at=conv.updated_at,
                    last_message_preview=preview,
                )
            )

        return items, total

    async def get_user_conversations(
        self,
        db: AsyncSession,
        user_id: int,
        tenant_id: int,
        page: int = 1,
        per_page: int = 20,
    ) -> tuple[list[ConversationListItem], int]:
        """Get user's conversations with pagination and last message preview (legacy method)"""
        return await self.get_conversations(
            db, tenant_id, page, per_page, user_id=user_id
        )

    async def create_conversation(
        self,
        db: AsyncSession,
        tenant_id: int,
        chatbot_config_id: int,
        title: str | None = None,
        user_id: int | None = None,
        chat_consumer_id: int | None = None,
        chatbot_config_version_index: int | None = None,
    ) -> Conversation:
        """Create new conversation"""
        db_conversation = Conversation(
            title=title,
            user_id=user_id,
            chat_consumer_id=chat_consumer_id,
            tenant_id=tenant_id,
            chatbot_config_id=chatbot_config_id,
            chatbot_config_version_index=chatbot_config_version_index,
        )
        db.add(db_conversation)
        await db.commit()
        await db.refresh(db_conversation)
        return db_conversation

    async def update_conversation_title(
        self, db: AsyncSession, conversation_id: int, title: str
    ) -> Conversation | None:
        """Update conversation title"""
        db_conversation = await self.get(db, conversation_id)
        if not db_conversation:
            return None

        db_conversation.title = title
        await db.commit()
        await db.refresh(db_conversation)
        return db_conversation

    async def update_conversation_timestamp(
        self, db: AsyncSession, conversation_id: int
    ) -> Conversation | None:
        """Update conversation updated_at timestamp"""
        db_conversation = await self.get(db, conversation_id)
        if not db_conversation:
            return None

        db_conversation.updated_at = datetime.utcnow()
        await db.commit()
        await db.refresh(db_conversation)
        return db_conversation

    async def delete_conversation(self, db: AsyncSession, conversation_id: int) -> bool:
        """Delete a conversation"""
        db_conversation = await self.get(db, conversation_id)
        if not db_conversation:
            return False

        await db.delete(db_conversation)
        await db.commit()
        return True


conversation = ConversationCRUD()
