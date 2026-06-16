"""
RAG (Retrieval-Augmented Generation) endpoints for querying documents.
"""

import time

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.crud.conversations import conversation
from app.crud.documents import document
from app.crud.messages import message
from app.models.chat_consumers import ChatConsumer
from app.schemas.rag import DocumentChunk, RAGQuery, RAGResponse
from app.schemas.users import User
from app.utils.dependencies import get_db, require_user_or_chat_consumer
from app.utils.response import success_response
from app.schemas.common import APIResponse
from app.services.chatbot_config_service import chatbot_config_service

router = APIRouter()


@router.post("/query", response_model=APIResponse[RAGResponse])
async def rag_query(
    rag_query: RAGQuery,
    db: AsyncSession = Depends(get_db),
    current_user_or_consumer: User | ChatConsumer = require_user_or_chat_consumer,
):
    """Perform RAG query against tenant's documents with LLM-generated response"""
    start_time = time.time()

    try:
        # Extract tenant_id and user/consumer info based on authentication type
        if isinstance(current_user_or_consumer, ChatConsumer):
            tenant_id = current_user_or_consumer.tenant_id
            user_id = None
            chat_consumer_id = current_user_or_consumer.id
        else:  # User
            tenant_id = current_user_or_consumer.tenant_id
            user_id = current_user_or_consumer.id
            chat_consumer_id = None

        # Get conversation history and chatbot_config_id if conversation_id is provided
        conversation_history = []
        chatbot_config_id = None
        version_index = None
        
        if rag_query.conversation_id:
            # Verify conversation exists and user/consumer owns it
            db_conversation = await conversation.get(
                db, conversation_id=rag_query.conversation_id
            )
            if not db_conversation:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Conversation not found",
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

            # Get chatbot_config_id and version_index from conversation
            chatbot_config_id = db_conversation.chatbot_config_id
            version_index = db_conversation.chatbot_config_version_index

            # Get recent messages for context
            conversation_history = await message.get_recent_messages_for_context(
                db, rag_query.conversation_id, limit=10
            )

        # If no chatbot_config_id from conversation, get default for tenant
        if not chatbot_config_id:
            default_chatbot = await chatbot_config_service.get_default_chatbot_config(
                db, tenant_id
            )
            if not default_chatbot:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="No chatbot configuration found for this tenant. Please create a chatbot first.",
                )
            chatbot_config_id = default_chatbot.id

        # Process RAG query with citations using tenant configuration
        from app.utils.rag import process_rag_query_with_citations

        response_text, citations, metadata = await process_rag_query_with_citations(
            db=db,
            query=rag_query.query,
            conversation_history=conversation_history,
            tenant_id=tenant_id,
            chatbot_config_id=chatbot_config_id,
            document_ids=None,  # Search all tenant documents
            top_k=rag_query.top_k or 5,
            # Use overrides only if explicitly provided in request
            override_temperature=rag_query.temperature,
            override_max_tokens=rag_query.max_tokens,
            version_index=version_index,
        )

        # Convert citations to DocumentChunk format if sources requested
        sources = []
        if rag_query.include_sources and citations:
            for citation in citations:
                # Extract document ID from URL
                doc_id = None
                if citation.document_url and "/documents/" in citation.document_url:
                    try:
                        doc_id = int(citation.document_url.split("/documents/")[1])
                    except (ValueError, IndexError):
                        pass

                if doc_id:
                    chunk = DocumentChunk(
                        document_id=doc_id,
                        document_title=citation.document_title,
                        chunk_content=citation.snippet,
                        similarity_score=1.0,  # Citations don't have scores
                        metadata=None,
                    )
                    sources.append(chunk)

        processing_time = time.time() - start_time

        # Extract images from metadata
        images = metadata.get("images", [])
        from app.schemas.rag import ImageResult
        image_results = [
            ImageResult(
                image_url=img.get("image_url", ""),
                caption=img.get("caption", ""),
                page_number=img.get("page_number"),
                similarity_score=img.get("similarity_score"),
            )
            for img in images
        ] if images else None

        # Create RAG response
        rag_response = RAGResponse(
            response=response_text,
            sources=sources if rag_query.include_sources else None,
            images=image_results,
            conversation_id=rag_query.conversation_id,
            tokens_used=metadata.get("tokens_used", 0),
            processing_time=processing_time,
        )

        # Save messages to conversation if conversation_id provided
        if rag_query.conversation_id:
            # Create user message
            await message.create_message(
                db,
                conversation_id=rag_query.conversation_id,
                role="user",
                content=rag_query.query,
            )

            # Create assistant response message
            await message.create_message(
                db,
                conversation_id=rag_query.conversation_id,
                role="assistant",
                content=response_text,
            )

            # Update conversation timestamp
            await conversation.update_conversation_timestamp(
                db, rag_query.conversation_id
            )

        return success_response(data=rag_response)

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"RAG query failed: {str(e)}",
        ) from e


@router.get("/documents/status")
async def get_document_status(
    db: AsyncSession = Depends(get_db),
    current_user_or_consumer: User | ChatConsumer = require_user_or_chat_consumer,
):
    """Get processing status of tenant's documents"""
    # Extract tenant_id based on authentication type
    if isinstance(current_user_or_consumer, ChatConsumer):
        tenant_id = current_user_or_consumer.tenant_id
    else:  # User
        tenant_id = current_user_or_consumer.tenant_id

    # Get all documents for the tenant
    tenant_documents = await document.get_by_tenant(db, tenant_id=tenant_id)

    status_summary = {
        "total": len(tenant_documents),
        "pending": 0,
        "processing": 0,
        "completed": 0,
        "failed": 0,
    }

    for doc in tenant_documents:
        status_summary[doc.processing_status.value] += 1

    return success_response(data=status_summary)
