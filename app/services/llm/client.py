"""Shared LLM API client configuration (OpenAI / OpenRouter)."""

import logging
from typing import Any

from openai import AsyncOpenAI

from app.core.config import settings

logger = logging.getLogger(__name__)

_async_client: AsyncOpenAI | None = None


def get_llm_api_key() -> str:
    key = settings.llm_api_key
    if not key:
        raise ValueError(
            f"API key required for LLM_API_PROVIDER={settings.LLM_API_PROVIDER!r}. "
            "Set OPENROUTER_API_KEY (or OPENAI_API_KEY when using openai)."
        )
    return key


def uses_openrouter() -> bool:
    return settings.LLM_API_PROVIDER == "openrouter"


def get_openrouter_headers() -> dict[str, str]:
    if not uses_openrouter():
        return {}
    headers: dict[str, str] = {}
    if settings.OPENROUTER_HTTP_REFERER:
        headers["HTTP-Referer"] = settings.OPENROUTER_HTTP_REFERER
    if settings.OPENROUTER_APP_NAME:
        headers["X-Title"] = settings.OPENROUTER_APP_NAME
    return headers


def normalize_model_name(model: str) -> str:
    """Normalize a model name for the active provider.

    OpenRouter expects a `provider/model` id; direct OpenAI expects a bare
    model name. Config defaults (e.g. "openai/gpt-4o-mini") were written for
    OpenRouter, so under LLM_API_PROVIDER=openai the prefix must be stripped
    before the name reaches the real OpenAI API.
    """
    if not model:
        return model
    if uses_openrouter():
        return model if "/" in model else f"openai/{model}"
    return model.split("/", 1)[1] if "/" in model else model


def strip_model_provider_prefix(model: str) -> str:
    """Strip provider/ prefix for model-family config lookup."""
    if "/" in model:
        return model.split("/", 1)[1]
    return model


def get_async_openai_client(**kwargs: Any) -> AsyncOpenAI:
    """Create an AsyncOpenAI client configured for the active LLM provider."""
    api_key = kwargs.pop("api_key", None) or get_llm_api_key()
    base_url = kwargs.pop("base_url", None) or settings.llm_api_base_url
    default_headers = kwargs.pop("default_headers", None)
    if default_headers is None:
        default_headers = get_openrouter_headers()

    client_kwargs: dict[str, Any] = {"api_key": api_key}
    if base_url:
        client_kwargs["base_url"] = base_url
    if default_headers:
        client_kwargs["default_headers"] = default_headers
    client_kwargs.update(kwargs)
    return AsyncOpenAI(**client_kwargs)


def get_cached_async_openai_client() -> AsyncOpenAI:
    global _async_client
    if _async_client is None:
        _async_client = get_async_openai_client()
    return _async_client


def get_chat_openai_kwargs() -> dict[str, Any]:
    """Keyword arguments for langchain_openai.ChatOpenAI."""
    kwargs: dict[str, Any] = {"api_key": get_llm_api_key()}
    if settings.llm_api_base_url:
        kwargs["base_url"] = settings.llm_api_base_url
    headers = get_openrouter_headers()
    if headers:
        kwargs["default_headers"] = headers
    return kwargs
