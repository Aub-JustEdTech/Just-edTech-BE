"""LLM service for chat completions and text generation using Factory Pattern"""

import logging
from typing import Any

from langsmith import traceable
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.llm.factory import LLMProviderFactory
from app.services.chatbot_config_service import chatbot_config_service

logger = logging.getLogger(__name__)


class LLMService:
    """
    Service for LLM chat completions and text generation.
    Uses Factory Pattern to support multiple LLM providers with different parameter requirements.
    """

    def __init__(self):
        """Initialize LLM service with provider factory"""
        self.provider_factory = LLMProviderFactory()

    @traceable(name="llm_generate_chat_completion")
    async def generate_chat_completion(
        self,
        messages: list[dict[str, str]],
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        provider: str | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """
        Generate chat completion using appropriate LLM provider.
        Automatically handles different parameter requirements for different models.

        Args:
            messages: List of message dicts with 'role' and 'content'
            model: Model name to use (defaults to gpt-4o-mini)
            temperature: Sampling temperature (0-2, defaults to 0.7)
            max_tokens: Maximum tokens to generate (defaults to 4096)
            provider: Provider name (auto-detected from model if not provided)
            **kwargs: Additional provider-specific parameters

        Returns:
            Dictionary with response content and metadata
        """
        try:
            # Use defaults if not provided
            model = model or "gpt-4o-mini"
            temperature = temperature if temperature is not None else 0.7
            max_tokens = max_tokens or 4096

            # Get appropriate provider
            if provider:
                llm_provider = self.provider_factory.create_provider(provider)
            else:
                llm_provider = self.provider_factory.get_provider_for_model(model)

            logger.info(
                f"Generating completion with model={model}, "
                f"temperature={temperature}, max_tokens={max_tokens}"
            )

            # Call provider's generate_completion method
            # Provider handles model-specific parameter mapping
            # Plumb optional request_timeout through kwargs if present
            if "request_timeout" in kwargs and kwargs["request_timeout"] is None:
                kwargs.pop("request_timeout")

            result = await llm_provider.generate_completion(
                messages=messages,
                model=model,
                temperature=temperature,
                max_tokens=max_tokens,
                **kwargs,
            )

            logger.info(
                f"Generated completion using {result['model']} "
                f"({result['usage']['total_tokens']} tokens)"
            )
            return result

        except Exception as e:
            logger.error(f"Error generating chat completion: {e}", exc_info=True)
            raise

    @traceable(name="llm_generate_with_config")
    async def generate_chat_completion_with_config(
        self,
        db: AsyncSession,
        chatbot_config_id: int,
        messages: list[dict[str, str]],
        override_model: str | None = None,
        override_temperature: float | None = None,
        override_max_tokens: int | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """
        Generate chat completion using chatbot-specific configuration.

        Args:
            db: Database session
            chatbot_config_id: Chatbot configuration ID for configuration lookup
            messages: List of message dicts with 'role' and 'content'
            override_model: Override chatbot's configured model
            override_temperature: Override chatbot's configured temperature
            override_max_tokens: Override chatbot's configured max_tokens
            **kwargs: Additional parameters for OpenAI API

        Returns:
            Dictionary with response content and metadata
        """

        # Get chatbot configuration
        chat_config = await chatbot_config_service.get_chat_model_config(db, chatbot_config_id)

        # Use overrides if provided, otherwise use tenant config
        model = override_model or chat_config["model"]
        temperature = (
            override_temperature
            if override_temperature is not None
            else chat_config["temperature"]
        )
        max_tokens = override_max_tokens or chat_config["max_tokens"]

        # Add system prompt if configured and not already in messages
        if chat_config["system_prompt"] and not any(
            msg.get("role") == "system" for msg in messages
        ):
            messages = [
                {"role": "system", "content": chat_config["system_prompt"]},
                *messages,
            ]

        logger.info(
            f"Generating completion for chatbot {chatbot_config_id} with model {model}, "
            f"temp={temperature}, max_tokens={max_tokens}"
        )

        # Attach request timeout from tenant config if not provided
        timeout_s = chat_config.get("timeout_s")
        if timeout_s is not None and "request_timeout" not in kwargs:
            kwargs["request_timeout"] = timeout_s

        return await self.generate_chat_completion(
            messages=messages,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            **kwargs,
        )


# Global LLM service instance
llm_service = LLMService()
