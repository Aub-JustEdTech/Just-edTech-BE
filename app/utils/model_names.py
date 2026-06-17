"""Shared helpers for normalizing LLM model names."""

import re


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
