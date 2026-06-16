"""
Chat service for orchestrating conversation and message operations.
"""


from sqlalchemy.ext.asyncio import AsyncSession

from app.crud.citations import citation
from app.crud.conversations import conversation
from app.crud.messages import message
from app.models.conversations import Conversation, Message
from app.schemas.messages import MessageResponse, SendMessageResponse
from app.services.agentic_rag.service import agentic_rag_service
from app.services.chatbot_config_service import chatbot_config_service
from app.services.token_tracking_service import token_tracking_service
from app.utils.rag import (
    generate_conversation_title,
    process_rag_query_with_citations,
)


class ChatService:
    """Service class to orchestrate chat functionality"""

    def __init__(self):
        pass

    async def send_message(
        self,
        db: AsyncSession,
        user_id: int,
        tenant_id: int,
        chatbot_config_id: int,
        content: str,
        conversation_id: int | None = None,
    ) -> tuple[SendMessageResponse, int]:
        """
        Main handler for user messages

        Returns:
            Tuple of (SendMessageResponse, conversation_id)
        """
        # Get or create conversation
        db_conversation = await self.get_or_create_conversation(
            db, user_id, tenant_id, chatbot_config_id, conversation_id
        )
        actual_conversation_id = db_conversation.id

        # Create user message
        user_message = await message.create_message(
            db, conversation_id=actual_conversation_id, role="user", content=content
        )

        # Generate title if this is the first message
        if not db_conversation.title:
            title = generate_conversation_title(content)
            await conversation.update_conversation_title(
                db, actual_conversation_id, title
            )

        # Get recent messages for context
        recent_messages = await message.get_recent_messages_for_context(
            db, actual_conversation_id, limit=10
        )

        # Use version_index from conversation to get the correct config version
        version_index = db_conversation.chatbot_config_version_index

        # Feature flag: use the agentic RAG pipeline when enabled in chatbot config.
        # Falls back to the classic single-pass RAG when disabled or not set.
        rag_config = await chatbot_config_service.get_rag_config(
            db, chatbot_config_id, version_index
        )
        use_agentic = rag_config.get("enable_agentic_rag", True)
        print(f"use_agentic_rag: {use_agentic}")

        if use_agentic:
            (
                bot_response,
                citations_data,
                metadata,
            ) = await agentic_rag_service.process_query(
                query=content,
                db=db,
                conversation_history=recent_messages,
                tenant_id=tenant_id,
                chatbot_config_id=chatbot_config_id,
                conversation_id=actual_conversation_id,
            )
        else:
            (
                bot_response,
                citations_data,
                metadata,
            ) = await process_rag_query_with_citations(
                db=db,
                query=content,
                conversation_history=recent_messages,
                tenant_id=tenant_id,
                chatbot_config_id=chatbot_config_id,
                version_index=version_index,
            )

        # Extract token information from metadata - always track for assistant messages
        input_tokens = metadata.get("input_tokens", 0)
        output_tokens = metadata.get("output_tokens", 0)
        total_tokens = metadata.get("tokens_used", 0)
        model_used = metadata.get("model", "unknown")

        # Always track tokens for assistant messages (LLM responses)
        # User messages don't have token usage from LLM calls, so they remain None

        # Create bot message with token tracking
        bot_message = await message.create_message(
            db,
            conversation_id=actual_conversation_id,
            role="assistant",
            content=bot_response,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=total_tokens,
            model_used=model_used,
        )

        # Track token usage in daily aggregation for cost monitoring
        if total_tokens > 0 and model_used != "unknown":
            await token_tracking_service.track_message_tokens(
                db=db,
                tenant_id=tenant_id,
                model_name=model_used,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                total_tokens=total_tokens,
            )

        # Create citations for bot message
        if citations_data:
            await citation.create_citations_bulk(db, bot_message.id, citations_data)

        # Update conversation timestamp
        await conversation.update_conversation_timestamp(db, actual_conversation_id)

        # Get the specific messages we just created with citations
        # This is more efficient than loading all messages and filtering
        specific_messages = await message.get_messages_by_ids(
            db, [user_message.id, bot_message.id]
        )

        # Create a mapping for easy lookup
        message_map = {msg.id: msg for msg in specific_messages}

        # Get the messages with citations, with fallback to the original messages
        user_msg_with_citations = message_map.get(user_message.id, user_message)
        bot_msg_with_citations = message_map.get(bot_message.id, bot_message)

        return (
            SendMessageResponse(
                user_message=MessageResponse.from_orm(user_msg_with_citations),
                bot_message=MessageResponse.from_orm(bot_msg_with_citations),
            ),
            actual_conversation_id,
        )

    async def get_or_create_conversation(
        self,
        db: AsyncSession,
        user_id: int,
        tenant_id: int,
        chatbot_config_id: int,
        conversation_id: int | None = None,
    ) -> Conversation:
        """Handle conversation creation/retrieval for first message flow"""
        if conversation_id is None or conversation_id == 0:
            # Get chatbot config and get latest version index for new conversation
            chatbot_config_obj = await chatbot_config_service.get_chatbot_config(
                db, chatbot_config_id
            )
            version_index = None
            if chatbot_config_obj:
                # Get latest version index (new conversations use the latest version)
                version_index = chatbot_config_service.get_latest_version_index(
                    chatbot_config_obj
                )

            # Create new conversation
            return await conversation.create_conversation(
                db,
                user_id=user_id,
                tenant_id=tenant_id,
                chatbot_config_id=chatbot_config_id,
                chatbot_config_version_index=version_index,
            )
        else:
            # Get existing conversation
            db_conversation = await conversation.get(db, conversation_id)
            if not db_conversation:
                raise ValueError("Conversation not found")

            # Verify ownership
            if (
                db_conversation.user_id != user_id
                or db_conversation.tenant_id != tenant_id
            ):
                raise ValueError("Access denied to conversation")

            return db_conversation

    async def build_context_from_history(
        self,
        db: AsyncSession,
        conversation_id: int,
        max_messages: int = 10,
    ) -> list[Message]:
        """Prepare context for LLM from conversation history"""
        return await message.get_recent_messages_for_context(
            db, conversation_id, limit=max_messages
        )

    async def get_conversation_with_messages(
        self,
        db: AsyncSession,
        conversation_id: int,
        user_id: int,
        tenant_id: int,
        page: int = 1,
        per_page: int = 50,
    ) -> tuple[Conversation, list[Message], int]:
        """Get conversation details with paginated messages"""
        # Get conversation
        db_conversation = await conversation.get(db, conversation_id)
        if not db_conversation:
            raise ValueError("Conversation not found")

        # Verify ownership
        if db_conversation.user_id != user_id or db_conversation.tenant_id != tenant_id:
            raise ValueError("Access denied to conversation")

        # Get messages
        messages, total = await message.get_conversation_messages(
            db, conversation_id, page, per_page
        )

        return db_conversation, messages, total


# Global chat service instance
chat_service = ChatService()
