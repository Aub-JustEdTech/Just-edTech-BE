"""
OpenAI LLM Provider Implementation.

Uses LangChain's `ChatOpenAI` so LangSmith traces render as chat messages
(separate Human / AI blocks) rather than a single function `Output`.
"""

import logging
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from openai import AsyncOpenAI

from app.core.config import settings
from app.services.llm.base import BaseLLMProvider

logger = logging.getLogger(__name__)


class OpenAIProvider(BaseLLMProvider):
    """OpenAI LLM provider with model-specific parameter handling"""

    # Model families and their parameter mappings
    MODEL_CONFIGS = {
        "gpt-3.5-turbo": {
            "max_tokens_param": "max_tokens",  # Older models use max_tokens
            "supports_system_prompt": True,
            "default_max_tokens": 4096,
            "supports_temperature": True,
            "default_temperature": 0.7,
            "temperature_range": (0, 2),
        },
        "gpt-4": {
            "max_tokens_param": "max_tokens",  # GPT-4 base uses max_tokens
            "supports_system_prompt": True,
            "default_max_tokens": 4096,
            "supports_temperature": True,
            "default_temperature": 0.7,
            "temperature_range": (0, 2),
        },
        "gpt-4-turbo": {
            "max_tokens_param": "max_tokens",  # GPT-4-turbo uses max_tokens
            "supports_system_prompt": True,
            "default_max_tokens": 4096,
            "supports_temperature": True,
            "default_temperature": 0.7,
            "temperature_range": (0, 2),
        },
        "gpt-4o": {
            # For this LangChain/OpenAI client combo, passing any
            # explicit max tokens param for gpt-4o tends to result
            # in the server receiving an unsupported `max_tokens`
            # field. For these models we bypass ChatOpenAI and call
            # the OpenAI client directly instead (see generate_completion).
            "max_tokens_param": None,
            "supports_system_prompt": True,
            "default_max_tokens": 4096,
            "supports_temperature": True,
            "default_temperature": 1.0,
            "temperature_range": (0, 2),
        },
        "gpt-4o-mini": {
            # Default model for most calls; see note above.
            # We bypass ChatOpenAI and call the OpenAI client directly
            # for this family, so we don't use a ChatOpenAI-level
            # max tokens parameter here.
            "max_tokens_param": None,
            "supports_system_prompt": True,
            "default_max_tokens": 16384,
            "supports_temperature": True,
            "default_temperature": 1.0,
            "temperature_range": (0, 2),
        },
        "gpt-5": {
            "max_tokens_param": None,  # Uses max_completion_tokens via direct OpenAI client
            "supports_system_prompt": False,
            "default_max_tokens": 16384,
            "supports_temperature": False,  # o1 models don't support temperature
            "default_temperature": 1.0,
            "temperature_range": None,
        },
        "o1-mini": {
            "max_tokens_param": None,  # Uses max_completion_tokens via direct OpenAI client
            "supports_system_prompt": False,
            "default_max_tokens": 16384,
            "supports_temperature": False,  # o1 models don't support temperature
            "default_temperature": 1.0,
            "temperature_range": None,
        },
        "o1-preview": {
            "max_tokens_param": None,  # Uses max_completion_tokens via direct OpenAI client
            "supports_system_prompt": False,
            "default_max_tokens": 16384,
            "supports_temperature": False,  # o1 models don't support temperature
            "default_temperature": 1.0,
            "temperature_range": None,
        },
        "o1": {
            "max_tokens_param": None,  # Uses max_completion_tokens via direct OpenAI client
            "supports_system_prompt": False,
            "default_max_tokens": 16384,
            "supports_temperature": False,  # o1 models don't support temperature
            "default_temperature": 1.0,
            "temperature_range": None,
        },
    }

    def __init__(self):
        if not settings.OPENAI_API_KEY:
            raise ValueError(
                "OPENAI_API_KEY is required but not provided. "
                "Please set it in your environment variables or .env file."
            )
        logger.info("OpenAI provider initialized (LangChain ChatOpenAI)")

    def _to_langchain_messages(self, messages: list[dict[str, str]]) -> list[BaseMessage]:
        lc_messages: list[BaseMessage] = []
        for msg in messages:
            role = (msg.get("role") or "").lower()
            content = msg.get("content") or ""
            if role == "system":
                lc_messages.append(SystemMessage(content=content))
            elif role == "assistant":
                lc_messages.append(AIMessage(content=content))
            else:
        # Treat unknown roles as user/human
                lc_messages.append(HumanMessage(content=content))
        return lc_messages

    def _build_chat_model(
        self,
        model: str,
        temperature: float | None,
        max_tokens: int,
        timeout_s: float | None,
        model_config: dict[str, Any],
    ) -> ChatOpenAI:
        # Only pass parameters the model family supports, to avoid API errors.
        kwargs: dict[str, Any] = {"model": model, "api_key": settings.OPENAI_API_KEY}

        if model_config.get("supports_temperature", True) and temperature is not None:
            kwargs["temperature"] = temperature

        # Respect per-model token parameter name to avoid API errors.
        # Some newer models (e.g., gpt-4o / gpt-4o-mini in this environment)
        # reject any explicit max tokens parameter; for those we skip it.
        max_tokens_param = model_config.get("max_tokens_param", "max_tokens")
        if max_tokens_param:
            kwargs[max_tokens_param] = max_tokens

        if timeout_s is not None:
            kwargs["timeout"] = timeout_s

        return ChatOpenAI(**kwargs)

    def _get_model_config(self, model_name: str) -> dict[str, Any]:
        """
        Get configuration for a specific model.

        Args:
            model_name: Full model name (e.g., "gpt-4-turbo-preview")

        Returns:
            Model configuration dictionary
        """
        # Check for exact match first
        if model_name in self.MODEL_CONFIGS:
            return self.MODEL_CONFIGS[model_name]

        # Check for prefix match (e.g., "gpt-4-turbo-preview" matches "gpt-4-turbo")
        for model_prefix, config in self.MODEL_CONFIGS.items():
            if model_name.startswith(model_prefix):
                return config

        # Default to GPT-4 config for unknown models (safer default)
        logger.warning(
            f"Unknown model {model_name}, using gpt-4o-mini parameter format as default"
        )
        return self.MODEL_CONFIGS["gpt-4o-mini"]

    async def generate_completion(
        self,
        messages: list[dict[str, str]],
        temperature: float,
        max_tokens: int,
        model: str = "gpt-4o-mini",
        **kwargs: Any,
    ) -> dict[str, Any]:
        """
        Generate chat completion using OpenAI.
        Automatically handles model-specific parameter requirements.

        Args:
            messages: List of message dicts
            temperature: Sampling temperature
            max_tokens: Maximum tokens to generate
            model: Model name to use
            **kwargs: Additional OpenAI-specific parameters

        Returns:
            Standardized response dictionary
        """
        try:
            # Get model-specific configuration
            model_config = self._get_model_config(model)

            logger.info(
                f"Calling OpenAI with model={model}, max_tokens={max_tokens}"
                + (f", temperature={temperature}" if model_config["supports_temperature"] else "")
            )

            # Handle system prompt based on model support
            effective_messages = messages
            if not model_config["supports_system_prompt"]:
                effective_messages = [msg for msg in messages if msg.get("role") != "system"]
                logger.info(f"Model {model} doesn't support system prompts, filtering them out")

            timeout_s = None
            if "request_timeout" in kwargs:
                timeout_s = kwargs.pop("request_timeout")

            # For models that require max_completion_tokens (gpt-4o, o1, etc.)
            # we bypass ChatOpenAI and call the OpenAI client directly
            # so we can control the payload and use max_completion_tokens.
            if model_config.get("max_tokens_param") is None:
                client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)
                openai_messages: list[dict[str, str]] = []
                for msg in effective_messages:
                    role = (msg.get("role") or "user").lower()
                    content = msg.get("content") or ""
                    # Normalize roles for OpenAI
                    if role not in ("system", "user", "assistant"):
                        role = "user"
                    openai_messages.append({"role": role, "content": content})

                # Build request parameters, only including supported ones
                request_params: dict[str, Any] = {
                    "model": model,
                    "messages": openai_messages,
                    "max_completion_tokens": max_tokens,
                }
                
                if model_config["supports_temperature"]:
                    request_params["temperature"] = temperature
                
                if timeout_s is not None:
                    request_params["timeout"] = timeout_s
                
                response = await client.chat.completions.create(**request_params)

                choice = response.choices[0]
                content = choice.message.content or ""
                usage = response.usage or None

                prompt_tokens = getattr(usage, "prompt_tokens", None) if usage else None
                completion_tokens = getattr(usage, "completion_tokens", None) if usage else None
                total_tokens = getattr(usage, "total_tokens", None) if usage else None
                if (
                    total_tokens is None
                    and prompt_tokens is not None
                    and completion_tokens is not None
                ):
                    total_tokens = prompt_tokens + completion_tokens

                result = {
                    "content": content,
                    "model": model,
                    "usage": {
                        "prompt_tokens": prompt_tokens or 0,
                        "completion_tokens": completion_tokens or 0,
                        "total_tokens": total_tokens or 0,
                    },
                    "finish_reason": getattr(choice, "finish_reason", "stop") or "stop",
                }

                logger.info(
                    f"OpenAI completion successful: {result['usage']['total_tokens']} tokens"
                )
                return result

            # Default path: use LangChain's ChatOpenAI wrapper.
            chat = self._build_chat_model(
                model=model,
                temperature=temperature if model_config["supports_temperature"] else None,
                max_tokens=max_tokens,
                timeout_s=timeout_s,
                model_config=model_config,
            )

            lc_messages = self._to_langchain_messages(effective_messages)
            ai_msg = await chat.ainvoke(lc_messages)

            # Usage metadata format differs across LangChain/OpenAI versions.
            usage = (
                (ai_msg.response_metadata or {}).get("token_usage")
                or (ai_msg.response_metadata or {}).get("usage")
                or {}
            )
            prompt_tokens = usage.get("prompt_tokens")
            completion_tokens = usage.get("completion_tokens")
            total_tokens = usage.get("total_tokens")
            if total_tokens is None and prompt_tokens is not None and completion_tokens is not None:
                total_tokens = prompt_tokens + completion_tokens

            result = {
                "content": ai_msg.content,
                "model": model,
                "usage": {
                    "prompt_tokens": prompt_tokens or 0,
                    "completion_tokens": completion_tokens or 0,
                    "total_tokens": total_tokens or 0,
                },
                "finish_reason": (ai_msg.response_metadata or {}).get("finish_reason", "stop"),
            }

            logger.info(
                f"OpenAI completion successful: {result['usage']['total_tokens']} tokens"
            )
            return result

        except Exception as e:
            logger.error(f"Error generating OpenAI completion: {e}", exc_info=True)
            raise

    def get_supported_parameters(self) -> dict[str, Any]:
        """Get supported parameters for OpenAI"""
        return {
            "temperature": {"type": "float", "min": 0, "max": 2, "default": 0.7},
            "max_tokens": {"type": "int", "min": 1, "max": 128000, "default": 4096},
            "top_p": {"type": "float", "min": 0, "max": 1, "default": 1},
            "stop": {"type": "list[str]", "default": None},
        }

    def validate_model(self, model_name: str) -> bool:
        """Validate if model is supported by OpenAI"""
        # Check if it's a known model or starts with a known prefix
        for model_prefix in self.MODEL_CONFIGS.keys():
            if model_name.startswith(model_prefix):
                return True

        # Allow unknown models but log warning
        logger.warning(f"Model {model_name} not in known list, allowing anyway")
        return True
