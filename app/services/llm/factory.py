"""
LLM Provider Factory
Creates appropriate LLM provider based on model/provider configuration.
"""

import logging

from app.services.llm.base import BaseLLMProvider
from app.services.llm.openai_provider import OpenAIProvider

logger = logging.getLogger(__name__)


class LLMProviderFactory:
    """Factory for creating LLM provider instances"""

    # Registry of available providers
    _providers = {
        "openai": OpenAIProvider,
        # Future providers can be added here:
        # "anthropic": AnthropicProvider,
        # "azure": AzureOpenAIProvider,
    }

    @classmethod
    def create_provider(cls, provider_name: str) -> BaseLLMProvider:
        """
        Create an LLM provider instance.

        Args:
            provider_name: Name of the provider (e.g., "openai", "anthropic")

        Returns:
            Instance of the appropriate provider

        Raises:
            ValueError: If provider is not supported
        """
        provider_name = provider_name.lower()

        if provider_name not in cls._providers:
            raise ValueError(
                f"Unsupported LLM provider: {provider_name}. "
                f"Supported providers: {list(cls._providers.keys())}"
            )

        provider_class = cls._providers[provider_name]
        logger.info(f"Creating LLM provider: {provider_name}")
        return provider_class()

    @classmethod
    def get_provider_for_model(cls, model_name: str) -> BaseLLMProvider:
        """
        Get appropriate provider based on model name.

        Args:
            model_name: Model name (e.g., "gpt-4", "claude-3")

        Returns:
            Instance of the appropriate provider
        """
        # Determine provider from model name
        if model_name.startswith("gpt"):
            return cls.create_provider("openai")
        elif model_name.startswith("claude"):
            # Future: return cls.create_provider("anthropic")
            raise ValueError("Anthropic provider not yet implemented")
        else:
            # Default to OpenAI for unknown models
            logger.warning(
                f"Unknown model prefix for {model_name}, defaulting to OpenAI provider"
            )
            return cls.create_provider("openai")

    @classmethod
    def register_provider(cls, name: str, provider_class: type[BaseLLMProvider]):
        """
        Register a new provider.

        Args:
            name: Provider name
            provider_class: Provider class (must inherit from BaseLLMProvider)
        """
        if not issubclass(provider_class, BaseLLMProvider):
            raise TypeError(
                f"Provider class must inherit from BaseLLMProvider, "
                f"got {provider_class}"
            )

        cls._providers[name.lower()] = provider_class
        logger.info(f"Registered new LLM provider: {name}")

    @classmethod
    def list_providers(cls) -> list[str]:
        """Get list of available providers"""
        return list(cls._providers.keys())
