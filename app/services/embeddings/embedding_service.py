"""Embedding generation service"""

import logging

from app.core.config import settings
from app.services.llm.client import get_cached_async_openai_client, normalize_model_name

logger = logging.getLogger(__name__)

_VALID_EMBEDDING_MODELS = {
    "text-embedding-3-small",
    "text-embedding-3-large",
    "text-embedding-ada-002",
    "openai/text-embedding-3-small",
    "openai/text-embedding-3-large",
    "openai/text-embedding-ada-002",
}

# OpenRouter caps embedding requests at 300k tokens; stay under with headroom.
_EMBEDDING_MAX_TOKENS_PER_REQUEST = 250_000
_EMBEDDING_MAX_TEXTS_PER_REQUEST = 128

# OpenAI's hard per-input cap for every embedding model above is 8192 tokens;
# one token of headroom avoids an off-by-one at the boundary. A single
# oversized chunk (e.g. a transcript segment with no natural break point)
# would otherwise get rejected outright and permanently fail the document
# after retries, since resending the same text always fails the same way.
_EMBEDDING_MAX_TOKENS_PER_TEXT = 8191

_TOKEN_ENCODER = None


def _get_token_encoder():
    """Cached tiktoken encoder for the cl100k_base BPE used by these models."""
    global _TOKEN_ENCODER
    if _TOKEN_ENCODER is None:
        import tiktoken

        _TOKEN_ENCODER = tiktoken.get_encoding("cl100k_base")
    return _TOKEN_ENCODER


def _truncate_to_token_limit(text: str, max_tokens: int) -> str:
    """Hard-truncate text to at most max_tokens BPE tokens."""
    encoder = _get_token_encoder()
    tokens = encoder.encode(text)
    if len(tokens) <= max_tokens:
        return text
    logger.warning(
        "Text is %s tokens, over the %s-token embedding limit; truncating "
        "(this text's stored/citation content is unaffected — only the "
        "embedded vector loses coverage of the truncated tail).",
        len(tokens),
        max_tokens,
    )
    return encoder.decode(tokens[:max_tokens])


def _estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)


def _batch_texts_for_embedding(texts: list[str]) -> list[list[str]]:
    """Split texts so each embedding API call stays within provider token limits."""
    batches: list[list[str]] = []
    current: list[str] = []
    current_tokens = 0

    for text in texts:
        text_tokens = _estimate_tokens(text)
        would_exceed_tokens = (
            current_tokens + text_tokens > _EMBEDDING_MAX_TOKENS_PER_REQUEST
        )
        would_exceed_count = len(current) >= _EMBEDDING_MAX_TEXTS_PER_REQUEST
        if current and (would_exceed_tokens or would_exceed_count):
            batches.append(current)
            current = []
            current_tokens = 0
        current.append(text)
        current_tokens += text_tokens

    if current:
        batches.append(current)
    return batches


class EmbeddingService:
    """Service for generating text embeddings"""

    def __init__(self):
        pass

    async def generate_embeddings(
        self, texts: list[str], model: str = None
    ) -> list[list[float]]:
        """
        Generate embeddings for list of texts.

        Args:
            texts: List of text strings to embed
            model: Embedding model to use (defaults to settings)

        Returns:
            List of embedding vectors
        """
        if not texts:
            return []

        model = normalize_model_name(model or settings.OPENAI_EMBEDDING_MODEL)

        if model not in _VALID_EMBEDDING_MODELS:
            logger.warning(
                f"Model '{model}' is not in the list of known valid models. "
                f"Valid models: {', '.join(sorted(_VALID_EMBEDDING_MODELS))}. "
                f"Attempting to use anyway..."
            )

        try:
            texts = [
                _truncate_to_token_limit(text, _EMBEDDING_MAX_TOKENS_PER_TEXT)
                for text in texts
            ]
            batches = _batch_texts_for_embedding(texts)
            logger.info(
                f"Generating embeddings for {len(texts)} texts "
                f"in {len(batches)} batch(es) using model: {model}"
            )
            client = get_cached_async_openai_client()
            embeddings: list[list[float]] = []
            for batch_idx, batch in enumerate(batches, start=1):
                logger.debug(
                    "Embedding batch %s/%s (%s texts)",
                    batch_idx,
                    len(batches),
                    len(batch),
                )
                response = await client.embeddings.create(input=batch, model=model)
                embeddings.extend(item.embedding for item in response.data)

            logger.info(f"Generated {len(embeddings)} embeddings using {model}")
            return embeddings

        except Exception as e:
            error_msg = (
                f"Error generating embeddings with model '{model}': {e}. "
                f"Please verify that: "
                f"1) The model name '{model}' is correct and available for your API key, "
                f"2) Your API key has access to this model, "
                f"3) The model is not deprecated. "
                f"Known embedding models: {', '.join(sorted(_VALID_EMBEDDING_MODELS))}"
            )
            logger.error(error_msg, exc_info=True)
            raise ValueError(error_msg) from e

    async def generate_single_embedding(
        self, text: str, model: str = None
    ) -> list[float]:
        """Generate embedding for single text"""
        embeddings = await self.generate_embeddings([text], model)
        return embeddings[0] if embeddings else []
