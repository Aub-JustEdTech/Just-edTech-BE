"""
RAG (Retrieval-Augmented Generation) utilities for document processing and querying.
"""

import logging
import re
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.documents import Document, ProcessingStatus
from app.schemas.citations import CitationCreate
from app.services.embeddings.embedding_service import EmbeddingService
from app.services.llm_service import LLMService
from app.services.chatbot_config_service import chatbot_config_service
from app.services.vector_store.factory import VectorStoreFactory

logger = logging.getLogger(__name__)


def normalize_model_name(model_name: str) -> str:
    """
    Normalize model name by removing version suffixes.

    Examples:
        gpt-4o-mini-2024-07-18 -> gpt-4o-mini
        gpt-4-turbo-2024-04-09 -> gpt-4-turbo
        gpt-3.5-turbo-0125 -> gpt-3.5-turbo

    Args:
        model_name: Full model name from API

    Returns:
        Normalized base model name
    """
    if not model_name or model_name == "unknown":
        return model_name

    # Remove date patterns (YYYY-MM-DD or YYYYMMDD)
    normalized = re.sub(r"-?\d{4}-?\d{2}-?\d{2}", "", model_name)

    # Remove version numbers at the end (like -0125, -0613, etc.)
    normalized = re.sub(r"-\d{4}$", "", normalized)

    # Remove trailing dashes
    normalized = normalized.rstrip("-")

    return normalized


class RAGProcessor:
    """RAG document processor and query handler using vector store and proper services"""

    def __init__(self):
        """Initialize RAG processor with vector store, embedding, and LLM services"""
        self.vector_store = VectorStoreFactory.create(settings.VECTOR_STORE_TYPE)
        self.embedding_service = EmbeddingService()
        self.llm_service = LLMService()

    async def query_documents(
        self,
        db: AsyncSession,
        query: str,
        tenant_id: int,
        chatbot_config_id: int,
        document_ids: list[int] | None = None,
        top_k: int = 5,
        version_index: int | None = None,
    ) -> list[dict[str, Any]]:
        """
        Query documents using vector similarity search.

        Args:
            db: Database session
            query: User query string
            tenant_id: Tenant ID for filtering documents
            chatbot_config_id: Chatbot configuration ID for embedding model
            document_ids: Optional list of specific document IDs to search
            top_k: Number of results to return
            version_index: Optional version index to retrieve config from history

        Returns:
            List of search results with chunks and metadata
        """
        try:
            logger.info(
                f"RAG query_documents called: query='{query[:50]}...', "
                f"tenant_id={tenant_id}, chatbot_config_id={chatbot_config_id}, document_ids={document_ids}, top_k={top_k}, version_index={version_index}"
            )

            # Get chatbot embedding configuration (with version_index if provided)
            embedding_config = await chatbot_config_service.get_embedding_model_config(
                db, chatbot_config_id, version_index
            )
            logger.debug(f"Using embedding model: {embedding_config.get('model')}")

            # Generate query embedding
            query_embedding = await self.embedding_service.generate_single_embedding(
                query, model=embedding_config["model"]
            )

            if not query_embedding:
                logger.error("Failed to generate query embedding")
                return []

            logger.debug(
                f"Generated query embedding with dimension: {len(query_embedding)}"
            )

            # Build filters for document IDs if provided
            filters = None
            if document_ids:
                logger.info(f"Filtering by document_ids: {document_ids}")
                # Get document UUIDs from IDs
                doc_result = await db.execute(
                    select(Document.doc_id).where(
                        Document.id.in_(document_ids),
                        Document.tenant_id == tenant_id,
                    )
                )
                doc_uuids = [row[0] for row in doc_result.all()]
                logger.info(
                    f"Found {len(doc_uuids)} document UUIDs: {doc_uuids[:5]}..."
                )

                if doc_uuids:
                    # Vector store filter format (works with both ChromaDB and Qdrant)
                    filters = {"document_id": {"$in": doc_uuids}}
                    logger.debug(f"Built filters: {filters}")
                else:
                    logger.warning(
                        f"No document UUIDs found for document_ids {document_ids}, "
                        f"tenant_id {tenant_id}. Search may return no results."
                    )
            else:
                logger.info("No document_ids filter - searching all tenant documents")

            # Search vector store
            logger.info(f"Searching vector store with top_k={top_k}, filters={filters}")
            search_results = await self.vector_store.search(
                query_embedding=query_embedding,
                tenant_id=tenant_id,
                limit=top_k,
                filters=filters,
            )

            logger.info(
                f"RAG query returned {len(search_results)} results for tenant {tenant_id}"
            )

            if search_results:
                logger.debug(
                    f"First result: score={search_results[0].get('score', 'N/A')}, "
                    f"document_id={search_results[0].get('metadata', {}).get('document_id', 'N/A')}"
                )
            else:
                logger.warning(
                    f"No results returned for query: '{query[:50]}...' "
                    f"(tenant_id={tenant_id}, top_k={top_k})"
                )

            return search_results

        except Exception as e:
            logger.error(f"Error querying documents: {e}", exc_info=True)
            return []


# Global RAG processor instance
rag_processor = RAGProcessor()


def generate_conversation_title(
    message_content: str, max_length: int = 50, max_words: int = 7
) -> str:
    """Generate conversation title from first message"""
    # Clean the content
    content = message_content.strip()

    # Split into words
    words = content.split()

    # Take first N words or truncate by character limit
    if len(words) <= max_words:
        title = " ".join(words)
    else:
        title = " ".join(words[:max_words])

    # Truncate by character limit
    if len(title) > max_length:
        title = title[:max_length].rsplit(" ", 1)[0]  # Don't cut words in half

    # Add ellipsis if truncated
    if len(title) < len(content):
        title += "..."

    return title


async def extract_citations_from_search_results(
    db: AsyncSession, search_results: list[dict[str, Any]]
) -> list[CitationCreate]:
    """
    Convert vector search results to citation format with actual document info.

    Args:
        db: Database session
        search_results: Results from vector store search

    Returns:
        List of CitationCreate objects
    """
    citations = []

    for i, result in enumerate(search_results):
        metadata = result.get("metadata", {})
        document_uuid = metadata.get("document_id")
        chunk_text = result.get("text", "")
        similarity_score = result.get("score", 0.0)
        page_number = metadata.get("page_number")

        # Get actual document info from database
        document_name = metadata.get("document_name", f"Document {document_uuid}")

        # Get the database document ID if available
        doc_result = await db.execute(
            select(Document.id).where(Document.doc_id == document_uuid)
        )
        db_doc_id = doc_result.scalar_one_or_none()

        # Create citation
        citation = CitationCreate(
            document_title=document_name,
            document_url=f"/documents/{db_doc_id}" if db_doc_id else "#",
            page_number=page_number,
            snippet=chunk_text[:500] + "..." if len(chunk_text) > 500 else chunk_text,
            position=i + 1,
        )
        citations.append(citation)

        logger.debug(
            f"Created citation {i+1}: {document_name} (score: {similarity_score:.3f})"
        )

    return citations


def _calculate_keyword_score(query: str, text: str) -> float:
    """
    Calculate keyword matching score between query and text.
    
    Args:
        query: User query string
        text: Text to match against (caption, surrounding text, etc.)
    
    Returns:
        Score between 0.0 and 1.0
    """
    if not query or not text:
        return 0.0
    
    # Normalize to lowercase
    query_lower = query.lower()
    text_lower = text.lower()
    
    # Extract words (simple tokenization)
    import re
    query_words = set(re.findall(r'\b\w+\b', query_lower))
    text_words = set(re.findall(r'\b\w+\b', text_lower))
    
    if not query_words:
        return 0.0
    
    # Calculate intersection (exact word matches)
    matches = query_words.intersection(text_words)
    exact_match_score = len(matches) / len(query_words)
    
    # Calculate substring matches (for partial word matches)
    substring_matches = sum(1 for qw in query_words if qw in text_lower)
    substring_score = substring_matches / len(query_words) * 0.5  # Weighted lower
    
    # Combine scores
    return min(1.0, exact_match_score + substring_score)


async def _retrieve_relevant_images(
    db: AsyncSession,
    query: str,
    tenant_id: int,
    chatbot_config_id: int,
    document_ids: list[int] | None,
    max_images: int = 1,
    version_index: int | None = None,
) -> list[dict[str, Any]]:
    """
    Retrieve relevant images based on query with re-ranking.

    Args:
        db: Database session
        query: User query string
        tenant_id: Tenant ID
        chatbot_config_id: Chatbot configuration ID
        document_ids: Optional list of document IDs to filter
        max_images: Maximum number of images to retrieve
        version_index: Optional version index for config

    Returns:
        List of image dictionaries with url, caption, etc.
    """
    from app.models.image_captions import ImageCaption
    from app.services.embeddings.embedding_service import EmbeddingService
    from app.services.vector_store.factory import VectorStoreFactory, VectorStoreType

    try:
        # Get embedding model config
        embedding_config = await chatbot_config_service.get_embedding_model_config(
            db, chatbot_config_id, version_index
        )
        embedding_model = embedding_config.get("model", settings.OPENAI_EMBEDDING_MODEL)

        # Generate query embedding
        embedding_service = EmbeddingService()
        query_embedding = await embedding_service.generate_single_embedding(
            query, model=embedding_model
        )

        if not query_embedding:
            logger.warning("Failed to generate query embedding for image search")
            return []

        # Get vector store
        vector_store = VectorStoreFactory.create(VectorStoreType(settings.VECTOR_STORE_TYPE))

        # Build filters for document IDs if provided
        filters = None
        if document_ids:
            # Get document UUIDs from IDs
            doc_result = await db.execute(
                select(Document.doc_id).where(
                    Document.id.in_(document_ids),
                    Document.tenant_id == tenant_id,
                )
            )
            doc_uuids = [row[0] for row in doc_result.all()]
            if doc_uuids:
                filters = {"document_id": {"$in": doc_uuids}}

        # Search for relevant images
        if not hasattr(vector_store, "search_images"):
            logger.warning("Vector store does not support image search")
            return []

        # Retrieve more candidates for re-ranking (3-5x the requested amount)
        candidate_limit = max(max_images * 3, 5)  # At least 5 candidates
        
        image_results = await vector_store.search_images(
            query_embedding=query_embedding,
            tenant_id=tenant_id,
            limit=candidate_limit,
            filters=filters,
        )

        if not image_results:
            return []

        # Get image caption records to get file paths
        image_caption_ids = [
            img["metadata"].get("image_caption_id")
            for img in image_results
            if img["metadata"].get("image_caption_id")
        ]

        if not image_caption_ids:
            return []

        # Fetch image caption records
        image_captions_result = await db.execute(
            select(ImageCaption).where(ImageCaption.id.in_(image_caption_ids))
        )
        image_captions = {ic.id: ic for ic in image_captions_result.scalars().all()}

        # Build image results with URLs and calculate re-ranking scores
        images_with_scores = []
        for img_result in image_results:
            caption_id = img_result["metadata"].get("image_caption_id")
            if not caption_id or caption_id not in image_captions:
                continue

            image_caption = image_captions[caption_id]
            
            # Generate image URL via API endpoint
            # Extract filename from image_file_path
            from pathlib import Path
            image_filename = Path(image_caption.image_file_path).name
            
            # Use API endpoint URL instead of file path
            image_url = f"{settings.API_V1_STR}/documents/images/{image_filename}"
            
            # If image_url is already set (e.g., S3 URL), use that instead
            if image_caption.image_url:
                image_url = image_caption.image_url

            # Get semantic similarity score (from vector search)
            semantic_score = img_result.get("score", 0.0)
            
            # Calculate keyword matching scores
            caption = img_result.get("caption", image_caption.caption)
            combined_text = img_result["metadata"].get("combined_text", caption)
            surrounding_before = image_caption.surrounding_text_before or ""
            surrounding_after = image_caption.surrounding_text_after or ""
            
            # Calculate keyword scores for different text components
            caption_keyword_score = _calculate_keyword_score(query, caption)
            combined_keyword_score = _calculate_keyword_score(query, combined_text)
            surrounding_keyword_score = _calculate_keyword_score(
                query, f"{surrounding_before} {surrounding_after}"
            )
            
            # Weighted keyword score (caption is most important)
            keyword_score = (
                caption_keyword_score * 0.5 +
                combined_keyword_score * 0.3 +
                surrounding_keyword_score * 0.2
            )
            
            # Combine semantic and keyword scores
            # Semantic score is typically 0-1 (cosine similarity)
            # Keyword score is 0-1
            # Weight: 70% semantic, 30% keyword (adjustable)
            final_score = semantic_score * 0.7 + keyword_score * 0.3

            images_with_scores.append(
                {
                    "image_url": image_url,
                    "caption": caption,
                    "page_number": image_caption.page_number,
                    "similarity_score": semantic_score,
                    "keyword_score": keyword_score,
                    "final_score": final_score,
                    "image_caption_id": caption_id,
                }
            )

        # Sort by final score (descending) and take top max_images
        images_with_scores.sort(key=lambda x: x["final_score"], reverse=True)
        top_images = images_with_scores[:max_images]
        
        # Return in expected format
        images = [
            {
                "image_url": img["image_url"],
                "caption": img["caption"],
                "page_number": img["page_number"],
                "similarity_score": img["final_score"],  # Return final score as similarity_score
            }
            for img in top_images
        ]

        logger.info(
            f"Retrieved {len(images)} images from {len(image_results)} candidates "
            f"(re-ranked by semantic + keyword matching)"
        )

        return images

    except Exception as e:
        logger.error(f"Error retrieving relevant images: {e}", exc_info=True)
        return []


def format_context_for_llm(messages: list, max_context_length: int = 4000) -> str:
    """Format conversation history for LLM context"""
    context_parts = []
    current_length = 0

    # Add messages in reverse order (most recent first) until we hit the limit
    for msg in reversed(messages):
        role = msg.role
        content = msg.content

        message_text = f"{role.upper()}: {content}\n"
        message_length = len(message_text)

        if current_length + message_length > max_context_length:
            break

        context_parts.insert(0, message_text)  # Insert at beginning to maintain order
        current_length += message_length

    return "\n".join(context_parts)


def _is_casual_query(query: str) -> bool:
    """
    Detect if a query is casual conversation or greeting that doesn't need RAG.
    
    Args:
        query: User query string
        
    Returns:
        True if query is casual/greeting, False if it's a document-related query
    """
    query_lower = query.lower().strip()
    
    # Remove common punctuation for matching
    query_normalized = query_lower.rstrip('!?.,:;')
    
    # Greetings and casual expressions
    casual_patterns = [
        # Greetings
        "hi", "hello", "hey", "howdy", "greetings", "good morning", "good afternoon", 
        "good evening", "sup", "what's up", "whats up", "yo",
        # Casual questions
        "how are you", "how are you doing", "how's it going", "hows it going",
        "what's new", "whats new", "how do you do",
        # Generic chat
        "thanks", "thank you", "ok", "okay", "cool", "nice", "bye", "goodbye",
        "see you", "later", "good night", "goodnight",
    ]
    
    # Check for exact matches (for short queries)
    if query_normalized in casual_patterns:
        return True
    
    # Check for patterns at the start of the query (e.g., "hey there" or "hello how are you")
    for pattern in casual_patterns:
        if query_normalized.startswith(pattern + " ") or query_normalized.startswith(pattern):
            # Allow some short follow-up text after greeting
            if len(query_normalized) <= len(pattern) + 20:
                return True
    
    return False


async def process_rag_query_with_citations(
    db: AsyncSession,
    query: str,
    conversation_history: list,
    tenant_id: int,
    chatbot_config_id: int,
    document_ids: list[int] | None = None,
    top_k: int = 5,
    override_model: str | None = None,
    override_temperature: float | None = None,
    override_max_tokens: int | None = None,
    version_index: int | None = None,
) -> tuple[str, list[CitationCreate], dict[str, Any]]:
    """
    Process user query with RAG and return response with citations.
    Uses chatbot-specific configuration for all parameters.

    Args:
        db: Database session
        query: User query string
        conversation_history: List of previous messages
        tenant_id: Tenant ID for document filtering
        chatbot_config_id: Chatbot configuration ID
        document_ids: Optional specific document IDs to search
        top_k: Number of chunks to retrieve
        override_model: Override chatbot's configured model
        override_temperature: Override chatbot's configured temperature
        override_max_tokens: Override chatbot's configured max_tokens
        version_index: Optional version index to retrieve config from history

    Returns:
        Tuple of (response_text, citations, metadata)
    """
    try:
        # Check if query is casual/greeting - let LLM respond without RAG
        if _is_casual_query(query):
            logger.info(f"Detected casual query: '{query[:50]}...', responding without RAG")
            
            # Get chatbot RAG configuration for system prompt
            rag_config = await chatbot_config_service.get_rag_config(
                db, chatbot_config_id, version_index
            )
            
            # Build system prompt for casual conversation
            system_prompt = rag_config.get("system_prompt") or """You are a helpful AI assistant with access to documents."""
            
            # Truncate conversation history to configured max, and send as structured messages
            max_history = rag_config.get("rag_max_history", 6)
            history_subset = (
                conversation_history[-max_history:] if max_history else conversation_history
            )
            history_messages = [
                {"role": msg.role, "content": msg.content} for msg in history_subset
            ]
            
            # Current user turn as its own message.
            # Note: Some callers (e.g. `send_message`) persist the user message first,
            # then pass recent_messages which already includes this query. Avoid duplicating.
            current_user_message = {"role": "user", "content": query}
            if history_messages:
                last = history_messages[-1]
                if last.get("role") == "user" and last.get("content") == query:
                    current_user_message = None
            
            messages = [{"role": "system", "content": system_prompt}]
            messages.extend(history_messages)
            if current_user_message is not None:
                messages.append(current_user_message)
            
            # Generate response using LLM
            llm_response = await rag_processor.llm_service.generate_chat_completion_with_config(
                db=db,
                chatbot_config_id=chatbot_config_id,
                messages=messages,
                override_model=override_model,
                override_temperature=override_temperature,
                override_max_tokens=override_max_tokens,
                request_timeout=rag_config.get("timeout_s"),
            )
            
            # Normalize model name
            raw_model = llm_response["model"]
            normalized_model = normalize_model_name(raw_model)
            
            return (
                llm_response["content"],
                [],
                {
                    "tokens_used": llm_response["usage"]["total_tokens"],
                    "input_tokens": llm_response["usage"]["prompt_tokens"],
                    "output_tokens": llm_response["usage"]["completion_tokens"],
                    "chunks_retrieved": 0,
                    "model": normalized_model,
                    "raw_model": raw_model,
                    "query_type": "casual",
                },
            )
        # Get chatbot RAG configuration (with version_index if provided)
        rag_config = await chatbot_config_service.get_rag_config(
            db, chatbot_config_id, version_index
        )

        # If no specific documents provided, get all completed documents for tenant
        if not document_ids:
            doc_result = await db.execute(
                select(Document.id).where(
                    Document.tenant_id == tenant_id,
                    Document.processing_status == ProcessingStatus.COMPLETED,
                )
            )
            document_ids = [row[0] for row in doc_result.all()]
            logger.info(
                f"Found {len(document_ids)} completed documents for tenant {tenant_id}"
            )

        if not document_ids:
            return (
                "I don't have any documents to search. Please upload and process documents first.",
                [],
                {
                    "tokens_used": 0,
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "chunks_retrieved": 0,
                    "model": "none",
                },
            )

        # Apply tenant-configured top_k if not overridden
        effective_top_k = rag_config.get("rag_top_k", top_k) or top_k

        # Query documents using RAG processor
        logger.info(
            f"Processing RAG query: query='{query[:50]}...', "
            f"tenant_id={tenant_id}, document_ids={document_ids}, "
            f"effective_top_k={effective_top_k}"
        )

        search_results = await rag_processor.query_documents(
            db=db,
            query=query,
            tenant_id=tenant_id,
            chatbot_config_id=chatbot_config_id,
            document_ids=document_ids,
            top_k=effective_top_k,
            version_index=version_index,
        )

        if not search_results:
            logger.warning(
                f"No search results found for query: '{query[:50]}...'. "
                f"This may indicate: 1) No matching documents, 2) Embedding mismatch, "
                f"3) Collection empty, or 4) Query too dissimilar to documents."
            )
            return (
                "I couldn't find any relevant information in the documents to answer your question. "
                "Please try rephrasing your query or ensure documents have been processed.",
                [],
                {
                    "tokens_used": 0,
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "chunks_retrieved": 0,
                    "model": "none",
                    "warning": "No search results found",
                },
            )

        logger.info(f"Retrieved {len(search_results)} chunks for RAG processing")

        # Extract citations
        citations = await extract_citations_from_search_results(db, search_results)

        # Retrieve relevant images if multimodal is enabled
        images = []
        enable_multimodal = rag_config.get("enable_multimodal", False)
        max_images = rag_config.get("max_images", 1)
        
        if enable_multimodal and max_images > 0 and settings.VECTOR_STORE_TYPE == "qdrant":
            try:
                images = await _retrieve_relevant_images(
                    db=db,
                    query=query,
                    tenant_id=tenant_id,
                    chatbot_config_id=chatbot_config_id,
                    document_ids=document_ids,
                    max_images=max_images,
                    version_index=version_index,
                )
                logger.info(f"Retrieved {len(images)} relevant images for query")
            except Exception as e:
                logger.warning(f"Error retrieving images: {e}", exc_info=True)
                # Don't fail the query if image retrieval fails

        # Build context from retrieved documents
        # Use full chunk text to preserve all information (chunks are already reasonably sized)
        # Apply intelligent truncation only if total context exceeds reasonable limits
        snippet_chars = rag_config.get("rag_snippet_chars", 2000)  # Increased default
        
        # Calculate total context size and truncate intelligently if needed
        max_total_context = 8000  # Maximum total context chars across all sources
        
        def _truncate_smart(text: str, limit: int) -> str:
            """Truncate text intelligently, preserving beginning which often has key info"""
            if not text:
                return ""
            if len(text) <= limit:
                return text
            # Keep the beginning (where names/titles often are) and truncate from middle
            # Use 80% for beginning, 20% for end context
            ellipsis = "... [truncated] ..."
            ellipsis_len = len(ellipsis)
            if limit <= ellipsis_len:
                # Not enough room for ellipsis plus context; hard truncate
                return text[:limit]
            usable_chars = limit - ellipsis_len
            begin_chars = int(usable_chars * 0.8)
            end_chars = usable_chars - begin_chars
            end_segment = text[-end_chars:] if end_chars > 0 else ""
            return text[:begin_chars] + ellipsis + end_segment
        
        # Build context with all chunks, but limit total size
        context_parts = []
        total_length = 0
        
        for i, result in enumerate(search_results):
            chunk_text = result.get('text', '')
            doc_name = result['metadata'].get('document_name', 'Unknown')
            prefix = f"[Source {i+1}] {doc_name}: "
            
            # Use full chunk if we have room, otherwise truncate
            formatted_chunk = prefix + chunk_text
            chunk_length_with_prefix = len(formatted_chunk)
            if total_length + chunk_length_with_prefix <= max_total_context:
                context_parts.append(formatted_chunk)
                total_length += chunk_length_with_prefix
            else:
                # Truncate this chunk to fit remaining space
                remaining_space = max_total_context - total_length - len(prefix) - 5  # Reserve for formatting
                if remaining_space > 200:  # Only add if meaningful space remains
                    truncated = _truncate_smart(chunk_text, min(remaining_space, snippet_chars))
                    formatted_chunk = prefix + truncated
                    context_parts.append(formatted_chunk)
                break  # Stop adding more chunks if we're at limit
        
        retrieved_context = "\n\n".join(context_parts)

        logger.info(
            f"Built retrieved_context with {len(search_results)} sources, "
            f"total length: {len(retrieved_context)} chars"
        )
        
        # Log preview of first chunk to verify content
        if search_results:
            first_chunk_preview = search_results[0].get('text', '')[:200]
            logger.debug(f"First chunk preview: {first_chunk_preview}...")

        # Truncate conversation history to configured max, and send as structured messages
        max_history = rag_config.get("rag_max_history", 6)
        history_subset = (
            conversation_history[-max_history:] if max_history else conversation_history
        )
        history_messages = [
            {"role": msg.role, "content": msg.content} for msg in history_subset
        ]

        # Build system prompt (use tenant's custom prompt if available, otherwise default)
        default_system_prompt = """You are a helpful AI assistant with access to documents. Use the provided context from documents and conversation history to answer the user's question accurately.

Guidelines:
- Reference specific sources when using information from documents (e.g., "According to Source 1...")
- If the documents don't contain relevant information, say so clearly
- Be concise but thorough in your responses
- Maintain conversation context from previous messages"""

        system_prompt = rag_config.get("system_prompt") or default_system_prompt
        context_embedded_in_system = False
        
        # Replace {context} placeholder in system prompt with retrieved context if present
        if "{context}" in system_prompt:
            system_prompt = system_prompt.replace("{context}", retrieved_context)
            context_embedded_in_system = True
            logger.info(
                f"Replaced {{context}} placeholder in system prompt. "
                f"Retrieved context length: {len(retrieved_context)} chars, "
                f"System prompt length after replacement: {len(system_prompt)} chars"
            )
        else:
            logger.debug(
                f"No {{context}} placeholder found in system prompt. "
                f"System prompt length: {len(system_prompt)} chars"
            )

        if context_embedded_in_system:
            # Retrieved context is already in the system prompt; just send the current question
            user_content = (
                f"Current Question: {query}\n\n"
                "IMPORTANT: Use the document context already embedded in the system "
                "prompt above. Only say you don't have information if the provided "
                "context truly lacks relevant details."
            )
        else:
            # Attach retrieved documents to the final user question
            user_content = (
                "Retrieved Documents:\n"
                f"{retrieved_context}\n\n"
                f"Current Question: {query}\n\n"
                "IMPORTANT: Please answer the question using the information provided "
                'in the "Retrieved Documents" section above. If the answer is in the '
                "retrieved documents, provide it directly. Only say you don't have "
                "information if the retrieved documents truly don't contain relevant "
                "information about the question."
            )

        # Log the final prompts being sent (first 200 chars for debugging)
        logger.debug(
            f"System prompt preview (first 200 chars): {system_prompt[:200]}...\n"
            f"User content preview (first 200 chars): {user_content[:200]}..."
        )

        # Generate response using LLM service with tenant configuration.
        # We send prior turns as individual messages so LangSmith can render
        # proper Human/AI separation instead of a single history blob.
        messages = [{"role": "system", "content": system_prompt}]
        messages.extend(history_messages)
        # Avoid duplicating the current user message if the caller already included it
        # in `conversation_history`.
        if not (
            history_messages
            and history_messages[-1].get("role") == "user"
            and history_messages[-1].get("content") == query
        ):
            messages.append({"role": "user", "content": user_content})

        llm_response = await rag_processor.llm_service.generate_chat_completion_with_config(
            db=db,
            chatbot_config_id=chatbot_config_id,
            messages=messages,
            override_model=override_model,
            override_temperature=override_temperature,
            override_max_tokens=override_max_tokens,
            request_timeout=rag_config.get("timeout_s"),
        )

        # Normalize model name to match database pricing
        # e.g., gpt-4o-mini-2024-07-18 -> gpt-4o-mini
        raw_model = llm_response["model"]
        normalized_model = normalize_model_name(raw_model)

        bot_response = llm_response["content"]
        metadata = {
            "tokens_used": llm_response["usage"]["total_tokens"],
            "input_tokens": llm_response["usage"]["prompt_tokens"],
            "output_tokens": llm_response["usage"]["completion_tokens"],
            "chunks_retrieved": len(search_results),
            "model": normalized_model,  # Use normalized model name
            "raw_model": raw_model,  # Keep original for reference
        }

        logger.info(
            f"RAG query completed: {metadata['chunks_retrieved']} chunks, "
            f"{metadata['tokens_used']} tokens, {len(images)} images"
        )

        # Add images to metadata for response
        metadata["images"] = images

        return bot_response, citations, metadata

    except Exception as e:
        logger.error(f"Error processing RAG query: {e}", exc_info=True)
        return (
            f"I apologize, but I encountered an error while processing your request: {str(e)}",
            [],
            {
                "tokens_used": 0,
                "input_tokens": 0,
                "output_tokens": 0,
                "chunks_retrieved": 0,
                "model": "error",
                "error": str(e),
            },
        )
