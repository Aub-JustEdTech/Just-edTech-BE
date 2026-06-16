"""
Chatbot Configuration Service - Centralized access to chatbot-specific settings.
Ensures DRY principle and modular configuration management.
"""

import json
import logging
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.crud.chatbot_configs import chatbot_config
from app.models.chatbot_configs import ChatbotConfig
from app.models.llm_models import LLMModel
from app.schemas.chatbot_configs import ChatbotConfigCreate, ChatbotConfigUpdate

logger = logging.getLogger(__name__)


class ChatbotConfigService:
    """
    Service for retrieving and managing chatbot-specific configurations.
    Provides centralized access to LLM models, parameters, and settings.
    """

    async def get_chatbot_config(
        self, db: AsyncSession, chatbot_config_id: int
    ) -> ChatbotConfig | None:
        """
        Get chatbot configuration.

        Args:
            db: Database session
            chatbot_config_id: Chatbot configuration ID

        Returns:
            ChatbotConfig object or None if not found
        """
        return await chatbot_config.get_with_relationships(db, chatbot_config_id)

    async def get_chat_model_config(
        self, db: AsyncSession, chatbot_config_id: int, version_index: int | None = None
    ) -> dict[str, Any]:
        """
        Get chat model configuration for chatbot.

        Args:
            db: Database session
            chatbot_config_id: Chatbot configuration ID
            version_index: Optional version index to retrieve from history. If None, uses current config.

        Returns:
            Dictionary with model name, temperature, max_tokens, and system_prompt
        """
        chatbot_config_obj = await chatbot_config.get(db, chatbot_config_id)

        if not chatbot_config_obj:
            logger.warning(
                f"No config found for chatbot {chatbot_config_id}, using defaults"
            )
            return self._get_default_chat_config()

        # Get config from version history
        # If version_index is provided, use that version; otherwise use latest version
        versioned_config = None
        if chatbot_config_obj.config_version_history:
            version_history = chatbot_config_obj.config_version_history
            if version_index is not None and 0 <= version_index < len(version_history):
                versioned_config = version_history[version_index].get("config")
            elif len(version_history) > 0:
                # Use latest version if version_index not provided
                versioned_config = version_history[-1].get("config")

        if not versioned_config:
            logger.warning(
                f"No config version found for chatbot {chatbot_config_id}, using defaults"
            )
            return self._get_default_chat_config()
        
        # Extract chat_model_id from versioned config
        chat_model_id = versioned_config.get("chat_model_id")

        # Get chat model details
        chat_model = None
        if chat_model_id:
            chat_model = await db.get(LLMModel, int(chat_model_id))

        # Extract values from versioned config
        temperature = versioned_config.get("temperature") or 0.7
        chat_max_tokens = versioned_config.get("chat_max_tokens") or settings.MAX_TOKENS
        system_prompt = versioned_config.get("system_prompt")
        openai_timeout_s = versioned_config.get("openai_timeout_s") or getattr(settings, "OPENAI_TIMEOUT_S", None)

        return {
            "model": chat_model.name
            if chat_model
            else settings.OPENAI_EMBEDDING_MODEL.replace("text-embedding", "gpt"),
            "temperature": temperature,
            "max_tokens": chat_max_tokens,
            "system_prompt": system_prompt,
            "provider": chat_model.provider if chat_model else "openai",
            "config": chat_model.config if chat_model else {},
            "timeout_s": openai_timeout_s,
        }

    async def get_embedding_model_config(
        self, db: AsyncSession, chatbot_config_id: int, version_index: int | None = None
    ) -> dict[str, Any]:
        """
        Get embedding model configuration for chatbot.

        Args:
            db: Database session
            chatbot_config_id: Chatbot configuration ID
            version_index: Optional version index to retrieve from history. If None, uses current config.

        Returns:
            Dictionary with model name and provider
        """
        chatbot_config_obj = await chatbot_config.get(db, chatbot_config_id)

        if not chatbot_config_obj:
            logger.warning(
                f"No config found for chatbot {chatbot_config_id}, using default"
            )
            return self._get_default_embedding_config()

        # Get config from version history
        # If version_index is provided, use that version; otherwise use latest version
        versioned_config = None
        if chatbot_config_obj.config_version_history:
            version_history = chatbot_config_obj.config_version_history
            if version_index is not None and 0 <= version_index < len(version_history):
                versioned_config = version_history[version_index].get("config")
            elif len(version_history) > 0:
                # Use latest version if version_index not provided
                versioned_config = version_history[-1].get("config")

        if not versioned_config:
            logger.warning(
                f"No config version found for chatbot {chatbot_config_id}, using default"
            )
            return self._get_default_embedding_config()

        # Extract embedding_model_id from versioned config
        embedding_model_id = versioned_config.get("embedding_model_id")

        if not embedding_model_id:
            logger.warning(
                f"No embedding model configured for chatbot {chatbot_config_id}, using default"
            )
            return self._get_default_embedding_config()

        embedding_model = await db.get(LLMModel, embedding_model_id)

        if not embedding_model:
            logger.error(f"Embedding model {embedding_model_id} not found")
            return self._get_default_embedding_config()

        return {
            "model": embedding_model.name,
            "provider": embedding_model.provider,
            "config": embedding_model.config or {},
        }

    async def get_rag_config(
        self, db: AsyncSession, chatbot_config_id: int, version_index: int | None = None
    ) -> dict[str, Any]:
        """
        Get complete RAG configuration for chatbot.

        Args:
            db: Database session
            chatbot_config_id: Chatbot configuration ID
            version_index: Optional version index to retrieve from history. If None, uses current config.

        Returns:
            Dictionary with all RAG-related settings
        """
        chatbot_config_obj = await chatbot_config.get(db, chatbot_config_id)

        if not chatbot_config_obj:
            logger.warning(
                f"No config found for chatbot {chatbot_config_id}, using defaults"
            )
            return self._get_default_rag_config()

        # Get config from version history
        # If version_index is provided, use that version; otherwise use latest version
        versioned_config = None
        if chatbot_config_obj.config_version_history:
            version_history = chatbot_config_obj.config_version_history
            if version_index is not None and 0 <= version_index < len(version_history):
                versioned_config = version_history[version_index].get("config")
            elif len(version_history) > 0:
                # Use latest version if version_index not provided
                versioned_config = version_history[-1].get("config")

        if not versioned_config:
            logger.warning(
                f"No config version found for chatbot {chatbot_config_id}, using defaults"
            )
            return self._get_default_rag_config()

        # Get both chat and embedding model configs (pass version_index)
        chat_config = await self.get_chat_model_config(
            db, chatbot_config_id, version_index
        )
        embedding_config = await self.get_embedding_model_config(
            db, chatbot_config_id, version_index
        )

        # Extract values from versioned config
        def get_value(key: str, default: Any = None) -> Any:
            return versioned_config.get(key) if versioned_config.get(key) is not None else default

        return {
            "chat_model": chat_config["model"],
            "chat_temperature": chat_config["temperature"],
            "chat_max_tokens": chat_config["max_tokens"],
            "system_prompt": chat_config["system_prompt"],
            "embedding_model": embedding_config["model"],
            "chunk_size": get_value("chunk_size", settings.CHUNK_SIZE),
            "chunk_overlap": get_value("chunk_overlap", settings.CHUNK_OVERLAP),
            "vector_store_type": get_value("vector_store_type", settings.VECTOR_STORE_TYPE),
            "search_type": get_value("search_type", "similarity"),
            "threshold_value": get_value("threshold_value", 0.7),
            "rag_top_k": get_value("rag_top_k", 3),
            "rag_max_history": get_value("rag_max_history", 6),
            "rag_context_chars": get_value("rag_context_chars", 2000),
            "rag_snippet_chars": get_value("rag_snippet_chars", 600),
            "timeout_s": chat_config.get("timeout_s"),
            "enable_multimodal": get_value(
                "enable_multimodal", settings.CHATBOT_DEFAULT_ENABLE_MULTIMODAL
            ),
            "max_images": get_value(
                "max_images", settings.CHATBOT_DEFAULT_MAX_IMAGES
            ),
            # Feature flag: whether to use the agentic RAG pipeline with tools.
            # Defaults to True so existing configs opt-in automatically unless
            # explicitly disabled in the versioned config.
            "enable_agentic_rag": get_value("enable_agentic_rag", True),
        }

    async def get_chunking_config(
        self, db: AsyncSession, chatbot_config_id: int
    ) -> dict[str, Any]:
        """
        Get document chunking configuration for chatbot.

        Args:
            db: Database session
            chatbot_config_id: Chatbot configuration ID

        Returns:
            Dictionary with chunk_size and chunk_overlap
        """
        # Use get_rag_config to retrieve chunk_size and chunk_overlap from version history
        rag_config = await self.get_rag_config(db, chatbot_config_id)
        
        return {
            "chunk_size": rag_config.get("chunk_size", settings.CHUNK_SIZE),
            "chunk_overlap": rag_config.get("chunk_overlap", settings.CHUNK_OVERLAP),
        }

    async def create_chatbot_config(
        self, db: AsyncSession, chatbot_config_create: ChatbotConfigCreate
    ) -> ChatbotConfig:
        """
        Create a new chatbot configuration.

        Args:
            db: Database session
            chatbot_config_create: Chatbot configuration creation schema

        Returns:
            Created ChatbotConfig object
        """
        return await chatbot_config.create(db, chatbot_config_create)

    async def list_chatbot_configs(
        self, db: AsyncSession, tenant_id: int
    ) -> list[ChatbotConfig]:
        """
        List all chatbot configurations for a tenant.

        Args:
            db: Database session
            tenant_id: Tenant ID

        Returns:
            List of ChatbotConfig objects
        """
        return await chatbot_config.list_by_tenant(db, tenant_id)

    async def get_default_chatbot_config(
        self, db: AsyncSession, tenant_id: int
    ) -> ChatbotConfig | None:
        """
        Get default chatbot configuration for a tenant.

        Args:
            db: Database session
            tenant_id: Tenant ID

        Returns:
            ChatbotConfig object or None if not found
        """
        return await chatbot_config.get_default(db, tenant_id)

    async def update_chatbot_config(
        self,
        db: AsyncSession,
        chatbot_config_id: int,
        chatbot_config_update: ChatbotConfigUpdate,
    ) -> ChatbotConfig | None:
        """
        Update chatbot configuration.

        Args:
            db: Database session
            chatbot_config_id: Chatbot configuration ID
            chatbot_config_update: Chatbot configuration update schema

        Returns:
            Updated ChatbotConfig object or None if not found
        """
        return await chatbot_config.update(db, chatbot_config_id, chatbot_config_update)

    async def delete_chatbot_config(
        self, db: AsyncSession, chatbot_config_id: int
    ) -> bool:
        """
        Delete chatbot configuration.

        Args:
            db: Database session
            chatbot_config_id: Chatbot configuration ID

        Returns:
            True if deleted, False if not found
        """
        return await chatbot_config.delete(db, chatbot_config_id)

    async def set_default_chatbot(
        self, db: AsyncSession, tenant_id: int, chatbot_config_id: int
    ) -> ChatbotConfig | None:
        """
        Set a chatbot as default for a tenant.

        Args:
            db: Database session
            tenant_id: Tenant ID
            chatbot_config_id: Chatbot configuration ID

        Returns:
            Updated ChatbotConfig object or None if not found
        """
        return await chatbot_config.set_default(db, tenant_id, chatbot_config_id)

    def get_latest_version_index(
        self, chatbot_config_obj: ChatbotConfig
    ) -> int:
        """
        Get the latest version index from chatbot configuration history.

        Args:
            chatbot_config_obj: ChatbotConfig object

        Returns:
            Latest version index, or 0 if no history exists
        """
        if (
            chatbot_config_obj.config_version_history
            and len(chatbot_config_obj.config_version_history) > 0
        ):
            return len(chatbot_config_obj.config_version_history) - 1
        return 0

    def _get_default_chat_config(self) -> dict[str, Any]:
        """Get default chat configuration when chatbot config is missing."""
        return {
            "model": "gpt-3.5-turbo",
            "temperature": 0.7,
            "max_tokens": settings.MAX_TOKENS,
            "system_prompt": None,
            "provider": "openai",
            "config": {},
        }

    def _get_default_embedding_config(self) -> dict[str, Any]:
        """Get default embedding configuration when chatbot config is missing."""
        return {
            "model": settings.OPENAI_EMBEDDING_MODEL,
            "provider": "openai",
            "config": {},
        }

    def _get_default_rag_config(self) -> dict[str, Any]:
        """Get default RAG configuration when chatbot config is missing."""
        return {
            "chat_model": "gpt-4o-mini",
            "chat_temperature": 0.7,
            "chat_max_tokens": settings.MAX_TOKENS,
            "system_prompt": None,
            "embedding_model": settings.OPENAI_EMBEDDING_MODEL,
            "chunk_size": settings.CHUNK_SIZE,
            "chunk_overlap": settings.CHUNK_OVERLAP,
            "vector_store_type": settings.VECTOR_STORE_TYPE,
            "search_type": "similarity",
            "threshold_value": 0.7,
            "rag_top_k": 3,
            "rag_max_history": 6,
            "rag_context_chars": 2000,
            "rag_snippet_chars": 600,
            "timeout_s": getattr(settings, "OPENAI_TIMEOUT_S", None),
            "enable_multimodal": settings.CHATBOT_DEFAULT_ENABLE_MULTIMODAL,
            "max_images": settings.CHATBOT_DEFAULT_MAX_IMAGES,
        }


# Global chatbot config service instance
chatbot_config_service = ChatbotConfigService()

