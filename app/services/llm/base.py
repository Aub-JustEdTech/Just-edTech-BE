"""
Base LLM Provider Interface
Defines the contract that all LLM providers must implement.
"""

from abc import ABC, abstractmethod
from typing import Any


class BaseLLMProvider(ABC):
    """Abstract base class for LLM providers"""

    @abstractmethod
    async def generate_completion(
        self,
        messages: list[dict[str, str]],
        temperature: float,
        max_tokens: int,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """
        Generate chat completion.

        Args:
            messages: List of message dicts with 'role' and 'content'
            temperature: Sampling temperature
            max_tokens: Maximum tokens to generate
            **kwargs: Additional provider-specific parameters

        Returns:
            Dictionary with standardized response:
            {
                "content": str,
                "model": str,
                "usage": {
                    "prompt_tokens": int,
                    "completion_tokens": int,
                    "total_tokens": int
                },
                "finish_reason": str
            }
        """
        pass

    @abstractmethod
    def get_supported_parameters(self) -> dict[str, Any]:
        """
        Get supported parameters for this provider/model.

        Returns:
            Dictionary mapping standard parameters to provider-specific ones
        """
        pass

    @abstractmethod
    def validate_model(self, model_name: str) -> bool:
        """
        Validate if model name is supported by this provider.

        Args:
            model_name: Model name to validate

        Returns:
            True if supported, False otherwise
        """
        pass
