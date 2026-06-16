"""
Conversation management endpoints for chat sessions.
"""

import logging
import math

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.crud.citations import citation
from app.crud.conversations import conversation
from app.crud.messages import message
from app.models.chat_consumers import ChatConsumer
from app.models.documents import Document
from app.schemas.conversations import (
    ConversationListResponse,
    ConversationResponse,
)
from app.schemas.messages import (
    MessageListResponse,
    MessageResponse,
    SendMessageRequest,
    SendMessageResponse,
)
from app.schemas.users import User
from app.services.agentic_rag.service import agentic_rag_service
from app.services.chatbot_config_service import chatbot_config_service
from app.services.conversation_report_service import conversation_report_service
from app.services.token_tracking_service import token_tracking_service
from app.utils.dependencies import (
    get_db,
    require_api_key_user_or_chat_consumer,
)
from app.utils.response import success_response
from app.utils.rag import generate_conversation_title, process_rag_query_with_citations
from app.utils.s3 import S3Manager

logger = logging.getLogger(__name__)
router = APIRouter()


async def _attach_presigned_urls_to_citations(
    db: AsyncSession,
    messages_with_citations: list[object],
    expires_in: int | None = None,
) -> None:
    """
    For each citation on the given messages, replace document_url with a presigned S3 URL
    (when possible) and attach an expires_in attribute for API responses.
    """
    if not messages_with_citations:
        return

    # Default expiry (1 hour) similar to documents presigned-url endpoint default
    effective_expires_in = expires_in or 3600

    # Cache Documents by ID to avoid repeated queries
    document_cache: dict[int, Document | None] = {}

    # Prepare S3 manager once
    s3_manager = S3Manager(
        bucket_name=settings.S3_BUCKET_NAME,
        region_name=settings.S3_REGION,
        aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
        aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
    )

    prefix = f"s3://{settings.S3_BUCKET_NAME}/"

    for msg in messages_with_citations:
        msg_citations = getattr(msg, "citations", None)
        if not msg_citations:
            continue

        for cit in msg_citations:
            raw_url = getattr(cit, "document_url", "") or ""
            if not raw_url.startswith("/documents/"):
                # Nothing we can do; leave as-is
                setattr(cit, "expires_in", None)
                continue

            # Extract document_id from "/documents/{id}"
            try:
                doc_id_str = raw_url.split("/documents/")[1].split("/")[0]
                document_id = int(doc_id_str)
            except Exception:
                setattr(cit, "expires_in", None)
                continue

            # Fetch from cache or DB
            if document_id not in document_cache:
                document_cache[document_id] = await db.get(Document, document_id)
            document = document_cache[document_id]

            if not document or not document.s3_url or not document.s3_url.startswith(
                prefix
            ):
                # Can't generate presigned URL; leave URL as-is
                setattr(cit, "expires_in", None)
                continue

            s3_key = document.s3_url[len(prefix) :]

            # Determine MIME type similar to documents.presigned-url endpoint
            ext = (document.document_type or "").lower()
            mime_map = {
                ".pdf": "application/pdf",
                ".md": "text/markdown; charset=utf-8",
                ".txt": "text/plain; charset=utf-8",
                ".doc": "application/msword",
                ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                ".xls": "application/vnd.ms-excel",
                ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            }
            content_type = mime_map.get(ext, "application/octet-stream")
            safe_name = (document.name or f"document_{document_id}").replace('"', "")
            content_disposition = f'inline; filename="{safe_name}"'

            try:
                presigned = await s3_manager.get_presigned_url(
                    s3_key=s3_key,
                    expiration=effective_expires_in,
                    http_method="GET",
                    response_content_type=content_type,
                    response_content_disposition=content_disposition,
                )
                page_number = getattr(cit, "page_number", None)
                if ext == ".pdf" and page_number:
                    cit.document_url = f"{presigned}#page={page_number}"
                else:
                    cit.document_url = presigned
                setattr(cit, "expires_in", effective_expires_in)
            except Exception:
                # On failure, fall back to original relative URL
                setattr(cit, "expires_in", None)


@router.get("/", response_model=ConversationListResponse)
async def list_conversations(
    page: int = Query(1, ge=1, description="Page number"),
    per_page: int = Query(20, ge=1, le=100, description="Items per page"),
    db: AsyncSession = Depends(get_db),
    current_user_or_consumer: User
    | ChatConsumer = require_api_key_user_or_chat_consumer,
):
    """Get user's or chat consumer's conversations with pagination"""
    # Extract tenant_id and user/consumer info based on authentication type
    if isinstance(current_user_or_consumer, ChatConsumer):
        tenant_id = current_user_or_consumer.tenant_id
        user_id = None
        chat_consumer_id = current_user_or_consumer.id
    else:  # User
        tenant_id = current_user_or_consumer.tenant_id
        user_id = current_user_or_consumer.id
        chat_consumer_id = None

    items, total = await conversation.get_conversations(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        chat_consumer_id=chat_consumer_id,
        page=page,
        per_page=per_page,
    )

    pages = math.ceil(total / per_page) if total > 0 else 1

    response_data = ConversationListResponse(
        items=items, total=total, page=page, per_page=per_page, pages=pages
    )
    return success_response(
        data=response_data,
        extra={"pagination": {"page": page, "per_page": per_page, "total": total, "pages": pages}},
    )


@router.get("/{conversation_id}", response_model=ConversationResponse)
async def get_conversation(
    conversation_id: int,
    db: AsyncSession = Depends(get_db),
    current_user_or_consumer: User
    | ChatConsumer = require_api_key_user_or_chat_consumer,
):
    """Get a specific conversation details"""
    db_conversation = await conversation.get(db, conversation_id=conversation_id)
    if not db_conversation:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found"
        )

    # Extract tenant_id and user/consumer info based on authentication type
    if isinstance(current_user_or_consumer, ChatConsumer):
        tenant_id = current_user_or_consumer.tenant_id
        user_id = None
        chat_consumer_id = current_user_or_consumer.id
    else:  # User
        tenant_id = current_user_or_consumer.tenant_id
        user_id = current_user_or_consumer.id
        chat_consumer_id = None

    # Check ownership based on authentication type
    if isinstance(current_user_or_consumer, ChatConsumer):
        # Chat consumer ownership check
        if (
            db_conversation.chat_consumer_id != chat_consumer_id
            or db_conversation.tenant_id != tenant_id
        ):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail="Not enough permissions"
            )
    else:
        # User ownership check
        if db_conversation.user_id != user_id or db_conversation.tenant_id != tenant_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail="Not enough permissions"
            )

    return success_response(
        data=ConversationResponse.model_validate(db_conversation)
    )


@router.delete("/{conversation_id}")
async def delete_conversation(
    conversation_id: int,
    db: AsyncSession = Depends(get_db),
    current_user_or_consumer: User
    | ChatConsumer = require_api_key_user_or_chat_consumer,
):
    """Delete a specific conversation"""
    db_conversation = await conversation.get(db, conversation_id=conversation_id)
    if not db_conversation:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found"
        )

    # Extract tenant_id and user/consumer info based on authentication type
    if isinstance(current_user_or_consumer, ChatConsumer):
        tenant_id = current_user_or_consumer.tenant_id
        user_id = None
        chat_consumer_id = current_user_or_consumer.id
    else:  # User
        tenant_id = current_user_or_consumer.tenant_id
        user_id = current_user_or_consumer.id
        chat_consumer_id = None

    # Check ownership based on authentication type
    if isinstance(current_user_or_consumer, ChatConsumer):
        # Chat consumer ownership check
        if (
            db_conversation.chat_consumer_id != chat_consumer_id
            or db_conversation.tenant_id != tenant_id
        ):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail="Not enough permissions"
            )
    else:
        # User ownership check
        if db_conversation.user_id != user_id or db_conversation.tenant_id != tenant_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail="Not enough permissions"
            )

    await conversation.delete_conversation(db, conversation_id)

    return success_response( data={"message": "Conversation deleted successfully"}, status_code=status.HTTP_200_OK )
    

@router.post("", response_model=SendMessageResponse, status_code=status.HTTP_201_CREATED)
async def create_conversation(
    message_request: SendMessageRequest,
    db: AsyncSession = Depends(get_db),
    current_user_or_consumer: User
    | ChatConsumer = require_api_key_user_or_chat_consumer,
):
    """Start a new conversation"""
    # Extract tenant_id and user/consumer info based on authentication type
    if isinstance(current_user_or_consumer, ChatConsumer):
        tenant_id = current_user_or_consumer.tenant_id
        user_id = None
        chat_consumer_id = current_user_or_consumer.id
    else:  # User
        tenant_id = current_user_or_consumer.tenant_id
        user_id = current_user_or_consumer.id
        chat_consumer_id = None

    # Get chatbot config and get latest version index for new conversation
    chatbot_config_obj = await chatbot_config_service.get_chatbot_config(
        db, message_request.chatbot_id
    )
    if not chatbot_config_obj:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Chatbot not found"
        )
    
    # Verify chatbot belongs to tenant
    if chatbot_config_obj.tenant_id != tenant_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Chatbot does not belong to your tenant",
        )
    
    # Get latest version index (new conversations use the latest version)
    version_index = chatbot_config_service.get_latest_version_index(chatbot_config_obj)
    
    # Create new conversation
    db_conversation = await conversation.create_conversation(
        db,
        tenant_id=tenant_id,
        chatbot_config_id=message_request.chatbot_id,
        user_id=user_id,
        chat_consumer_id=chat_consumer_id,
        chatbot_config_version_index=version_index,
    )
    conversation_id = db_conversation.id

    # Create user message
    user_message = await message.create_message(
        db,
        conversation_id=conversation_id,
        role="user",
        content=message_request.content,
    )

    # Generate title
    title = generate_conversation_title(message_request.content)
    await conversation.update_conversation_title(db, conversation_id, title)

    # Get recent messages for context (limit to 10)
    recent_messages = await message.get_recent_messages_for_context(
        db, conversation_id, limit=10
    )

    # Process query using either the agentic RAG pipeline or the classic single-pass RAG,
    # based on the chatbot configuration feature flag enable_agentic_rag.
    # Use version_index from conversation to get the correct config version.
    version_index = db_conversation.chatbot_config_version_index
    rag_config = await chatbot_config_service.get_rag_config(
        db, db_conversation.chatbot_config_id, version_index
    )
    use_agentic = rag_config.get("enable_agentic_rag", True)

    if use_agentic:
        bot_response, citations_data, metadata = await agentic_rag_service.process_query(
            query=message_request.content,
            db=db,
            conversation_history=recent_messages,
            tenant_id=tenant_id,
            chatbot_config_id=db_conversation.chatbot_config_id,
            conversation_id=conversation_id,
        )
    else:
        bot_response, citations_data, metadata = (
            await process_rag_query_with_citations(
                db=db,
                query=message_request.content,
                conversation_history=recent_messages,
                tenant_id=tenant_id,
                chatbot_config_id=db_conversation.chatbot_config_id,
                version_index=version_index,
            )
        )

    # Extract token information from metadata
    input_tokens = metadata.get("input_tokens", 0)
    output_tokens = metadata.get("output_tokens", 0)
    total_tokens = metadata.get("tokens_used", 0)
    model_used = metadata.get("model", "unknown")
    
    # Extract images from metadata
    images_data = metadata.get("images", [])

    # Create bot message with token tracking and images
    bot_message = await message.create_message(
        db,
        conversation_id=conversation_id,
        role="assistant",
        content=bot_response,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=total_tokens,
        model_used=model_used,
        images=images_data if images_data else None,
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
    await conversation.update_conversation_timestamp(db, conversation_id)

    # Get the specific messages we just created with citations
    specific_messages = await message.get_messages_by_ids(
        db, [user_message.id, bot_message.id]
    )

    # Create a mapping for easy lookup
    message_map = {msg.id: msg for msg in specific_messages}

    # Get the messages with citations, with fallback to the original messages
    user_msg_with_citations = message_map.get(user_message.id, user_message)
    bot_msg_with_citations = message_map.get(bot_message.id, bot_message)

    # Attach presigned URLs + expiry to citations on the bot message
    await _attach_presigned_urls_to_citations(
        db, [bot_msg_with_citations]
    )

    return success_response(
        data=SendMessageResponse(
            user_message=MessageResponse.model_validate(user_msg_with_citations),
            bot_message=MessageResponse.model_validate(bot_msg_with_citations),
        ),
        status_code=status.HTTP_201_CREATED,
    )


@router.post("/{conversation_id}/messages", response_model=SendMessageResponse)
async def send_message(
    conversation_id: int,
    message_request: SendMessageRequest,
    db: AsyncSession = Depends(get_db),
    current_user_or_consumer: User
    | ChatConsumer = require_api_key_user_or_chat_consumer,
):
    """Send a message to an existing conversation"""
    # Extract tenant_id and user/consumer info based on authentication type
    if isinstance(current_user_or_consumer, ChatConsumer):
        tenant_id = current_user_or_consumer.tenant_id
        user_id = None
        chat_consumer_id = current_user_or_consumer.id
    else:  # User
        tenant_id = current_user_or_consumer.tenant_id
        user_id = current_user_or_consumer.id
        chat_consumer_id = None

    # Get existing conversation
    db_conversation = await conversation.get(db, conversation_id=conversation_id)
    if not db_conversation:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found"
        )

    # Check ownership based on authentication type
    if isinstance(current_user_or_consumer, ChatConsumer):
        # Chat consumer ownership check
        if (
            db_conversation.chat_consumer_id != chat_consumer_id
            or db_conversation.tenant_id != tenant_id
        ):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Not enough permissions",
            )
    else:
        # User ownership check
        if (
            db_conversation.user_id != user_id
            or db_conversation.tenant_id != tenant_id
        ):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Not enough permissions",
            )
    
    # For existing conversations, validate chatbot_id matches
    if db_conversation.chatbot_config_id != message_request.chatbot_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Chatbot ID does not match conversation's chatbot",
        )

    # Create user message
    user_message = await message.create_message(
        db,
        conversation_id=conversation_id,
        role="user",
        content=message_request.content,
    )

    # Generate title if this is the first message
    if not db_conversation.title:
        title = generate_conversation_title(message_request.content)
        await conversation.update_conversation_title(db, conversation_id, title)

    # Get recent messages for context
    recent_messages = await message.get_recent_messages_for_context(
        db, conversation_id, limit=10
    )

    # Process query using either the agentic RAG pipeline or the classic single-pass RAG,
    # based on the chatbot configuration feature flag enable_agentic_rag.
    # Use version_index from conversation to get the correct config version.
    version_index = db_conversation.chatbot_config_version_index
    rag_config = await chatbot_config_service.get_rag_config(
        db, db_conversation.chatbot_config_id, version_index
    )
    use_agentic = rag_config.get("enable_agentic_rag", True)

    if use_agentic:
        bot_response, citations_data, metadata = await agentic_rag_service.process_query(
            query=message_request.content,
            db=db,
            conversation_history=recent_messages,
            tenant_id=tenant_id,
            chatbot_config_id=db_conversation.chatbot_config_id,
            conversation_id=conversation_id,
        )
    else:
        bot_response, citations_data, metadata = (
            await process_rag_query_with_citations(
                db=db,
                query=message_request.content,
                conversation_history=recent_messages,
                tenant_id=tenant_id,
                chatbot_config_id=db_conversation.chatbot_config_id,
                version_index=version_index,
            )
        )

    # Extract token information from metadata
    input_tokens = metadata.get("input_tokens", 0)
    output_tokens = metadata.get("output_tokens", 0)
    total_tokens = metadata.get("tokens_used", 0)
    model_used = metadata.get("model", "unknown")
    
    # Extract images from metadata
    images_data = metadata.get("images", [])

    # Create bot message with token tracking and images
    bot_message = await message.create_message(
        db,
        conversation_id=conversation_id,
        role="assistant",
        content=bot_response,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=total_tokens,
        model_used=model_used,
        images=images_data if images_data else None,
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
    await conversation.update_conversation_timestamp(db, conversation_id)

    # Get the specific messages we just created with citations
    # This avoids the IndexError that can occur in long conversations where
    # the newly created messages might not be in the first 1000 messages
    specific_messages = await message.get_messages_by_ids(
        db, [user_message.id, bot_message.id]
    )

    # Create a mapping for easy lookup
    message_map = {msg.id: msg for msg in specific_messages}

    # Get the messages with citations, with fallback to the original messages
    user_msg_with_citations = message_map.get(user_message.id, user_message)
    bot_msg_with_citations = message_map.get(bot_message.id, bot_message)

    # Attach presigned URLs + expiry to citations on the bot message
    await _attach_presigned_urls_to_citations(
        db, [bot_msg_with_citations]
    )

    return success_response(
        data=SendMessageResponse(
            user_message=MessageResponse.model_validate(user_msg_with_citations),
            bot_message=MessageResponse.model_validate(bot_msg_with_citations),
        )
    )


@router.get("/{conversation_id}/messages", response_model=MessageListResponse)
async def get_conversation_messages(
    conversation_id: int,
    page: int = Query(1, ge=1, description="Page number"),
    per_page: int = Query(50, ge=1, le=100, description="Items per page"),
    db: AsyncSession = Depends(get_db),
    current_user_or_consumer: User
    | ChatConsumer = require_api_key_user_or_chat_consumer,
):
    """Get paginated message history for a conversation"""
    # Extract tenant_id and user/consumer info based on authentication type
    if isinstance(current_user_or_consumer, ChatConsumer):
        tenant_id = current_user_or_consumer.tenant_id
        user_id = None
        chat_consumer_id = current_user_or_consumer.id
    else:  # User
        tenant_id = current_user_or_consumer.tenant_id
        user_id = current_user_or_consumer.id
        chat_consumer_id = None

    # Verify conversation exists and user/consumer owns it
    db_conversation = await conversation.get(db, conversation_id=conversation_id)
    if not db_conversation:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found"
        )

    # Check ownership based on authentication type
    if isinstance(current_user_or_consumer, ChatConsumer):
        # Chat consumer ownership check
        if (
            db_conversation.chat_consumer_id != chat_consumer_id
            or db_conversation.tenant_id != tenant_id
        ):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail="Not enough permissions"
            )
    else:
        # User ownership check
        if db_conversation.user_id != user_id or db_conversation.tenant_id != tenant_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail="Not enough permissions"
            )

    # Get paginated messages
    messages, total = await message.get_conversation_messages(
        db, conversation_id, page, per_page
    )

    pages = math.ceil(total / per_page) if total > 0 else 1

    # Attach presigned URLs + expiry to citations on all messages in the page
    await _attach_presigned_urls_to_citations(db, list(messages))

    message_responses = [MessageResponse.model_validate(msg) for msg in messages]

    response_data = MessageListResponse(
        items=message_responses, total=total, page=page, per_page=per_page, pages=pages
    )
    return success_response(
        data=response_data,
        extra={"pagination": {"page": page, "per_page": per_page, "total": total, "pages": pages}},
    )


@router.get("/{conversation_id}/report")
async def get_conversation_report(
    conversation_id: int,
    db: AsyncSession = Depends(get_db),
    current_user_or_consumer: User
    | ChatConsumer = require_api_key_user_or_chat_consumer,
):
    """
    Generate and download a structured PDF report for a conversation.

    Access rules mirror normal conversation access: anyone who can view the
    conversation can generate its report.
    """
    # Extract tenant_id and user/consumer info based on authentication type
    if isinstance(current_user_or_consumer, ChatConsumer):
        tenant_id = current_user_or_consumer.tenant_id
        user_id = None
        chat_consumer_id = current_user_or_consumer.id
    else:  # User
        tenant_id = current_user_or_consumer.tenant_id
        user_id = current_user_or_consumer.id
        chat_consumer_id = None

    # Verify conversation exists and user/consumer owns it
    db_conversation = await conversation.get(db, conversation_id=conversation_id)
    if not db_conversation:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found"
        )

    # Check ownership based on authentication type
    if isinstance(current_user_or_consumer, ChatConsumer):
        if (
            db_conversation.chat_consumer_id != chat_consumer_id
            or db_conversation.tenant_id != tenant_id
        ):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Not enough permissions",
            )
    else:
        if db_conversation.user_id != user_id or db_conversation.tenant_id != tenant_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Not enough permissions",
            )

    try:
        logger.info(f"Generating report for conversation {conversation_id}")
        filename, pdf_buffer = await conversation_report_service.generate_report_pdf_for_conversation(
            db=db,
            conversation_id=conversation_id,
        )
        logger.info(f"Report generated successfully: {filename}, size: {len(pdf_buffer.getvalue())} bytes")
    except ValueError as exc:
        logger.error(f"ValueError generating report for conversation {conversation_id}: {exc}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    except Exception as exc:  # pragma: no cover - defensive
        logger.error(f"Exception generating report for conversation {conversation_id}: {exc}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to generate conversation report: {str(exc)}",
        ) from exc

    return StreamingResponse(
        pdf_buffer,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
        },
    )
