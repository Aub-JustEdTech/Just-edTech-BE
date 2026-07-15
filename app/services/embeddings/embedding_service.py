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
            logger.info(f"Generating embeddings for {len(texts)} texts using model: {model}")
            client = get_cached_async_openai_client()
            response = await client.embeddings.create(input=texts, model=model)
            embeddings = [item.embedding for item in response.data]

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
