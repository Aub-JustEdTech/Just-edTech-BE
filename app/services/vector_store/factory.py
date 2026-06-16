"""
Factory for creating vector store instances.
"""

import logging
from enum import Enum

from app.core.config import settings
from app.services.vector_store.base import VectorStore

logger = logging.getLogger(__name__)


class VectorStoreType(str, Enum):
    """Supported vector store types"""

    CHROMA = "chroma"
    QDRANT = "qdrant"
    PINECONE = "pinecone"
    WEAVIATE = "weaviate"
    PGVECTOR = "pgvector"


class VectorStoreFactory:
    """Factory to create vector store instances based on configuration"""

    @staticmethod
    def create(store_type: str | VectorStoreType = None) -> VectorStore:
        """
        Create a vector store instance.

        Args:
            store_type: Type of vector store to create (defaults to settings.VECTOR_STORE_TYPE)
                        Can be a string or VectorStoreType enum

        Returns:
            VectorStore instance

        Raises:
            ValueError: If store_type is not supported
        """
        if store_type is None:
            store_type = settings.VECTOR_STORE_TYPE

        if isinstance(store_type, VectorStoreType):
            store_type = store_type.value
        store_type = str(store_type).lower()

        if store_type == VectorStoreType.CHROMA:
            from app.services.vector_store.chroma_store import ChromaDBStore

            logger.info("Creating ChromaDB vector store")
            return ChromaDBStore(persist_directory=settings.CHROMA_PERSIST_DIR)

        elif store_type == VectorStoreType.QDRANT:
            from app.services.vector_store.qdrant_store import QdrantStore

            logger.info("Creating Qdrant vector store")
            return QdrantStore(url=settings.QDRANT_URL)

        else:
            raise ValueError(
                f"Unsupported vector store type: {store_type}. "
                f"Supported types: {[e.value for e in VectorStoreType]}"
            )
