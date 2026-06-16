"""Embedding generation service"""

import logging

from openai import AsyncOpenAI

from app.core.config import settings

logger = logging.getLogger(__name__)

_async_client: AsyncOpenAI | None = None


def _get_client() -> AsyncOpenAI:
    global _async_client
    if _async_client is None:
        _async_client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)
    return _async_client


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

        model = model or settings.OPENAI_EMBEDDING_MODEL

        valid_models = [
            "text-embedding-3-small",
            "text-embedding-3-large",
            "text-embedding-ada-002",
        ]
        
        if model not in valid_models:
            logger.warning(
                f"Model '{model}' is not in the list of known valid models. "
                f"Valid models: {', '.join(valid_models)}. "
                f"Attempting to use anyway..."
            )

        try:
            logger.info(f"Generating embeddings for {len(texts)} texts using model: {model}")
            client = _get_client()
            response = await client.embeddings.create(input=texts, model=model)
            embeddings = [item.embedding for item in response.data]

            logger.info(f"Generated {len(embeddings)} embeddings using {model}")
            return embeddings

        except Exception as e:
            error_msg = (
                f"Error generating embeddings with model '{model}': {e}. "
                f"Please verify that: "
                f"1) The model name '{model}' is correct and available for your OpenAI API key, "
                f"2) Your API key has access to this model, "
                f"3) The model is not deprecated. "
                f"Valid OpenAI embedding models: {', '.join(valid_models)}"
            )
            logger.error(error_msg, exc_info=True)
            raise ValueError(error_msg) from e

    async def generate_single_embedding(
        self, text: str, model: str = None
    ) -> list[float]:
        """Generate embedding for single text"""
        embeddings = await self.generate_embeddings([text], model)
        return embeddings[0] if embeddings else []
