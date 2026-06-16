"""
Abstract base class for vector store implementations.
"""

from abc import ABC, abstractmethod
from typing import Any


class VectorStore(ABC):
    """
    Abstract interface for vector database operations.
    All vector store implementations (ChromaDB, Pinecone, Weaviate, etc.) must implement this interface.
    """

    @abstractmethod
    async def add_chunks(
        self,
        document_id: str,
        chunks: list[str],
        embeddings: list[list[float]],
        metadatas: list[dict[str, Any]],
    ) -> bool:
        """
        Store document chunks with their embeddings and metadata.

        Args:
            document_id: Unique identifier for the document
            chunks: List of text chunks
            embeddings: List of embedding vectors (one per chunk)
            metadatas: List of metadata dicts (one per chunk)

        Returns:
            bool: True if successful, False otherwise
        """
        pass

    @abstractmethod
    async def search(
        self,
        query_embedding: list[float],
        tenant_id: int,
        limit: int = 5,
        filters: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """
        Search for similar chunks using vector similarity.

        Args:
            query_embedding: Query vector
            tenant_id: Tenant ID to filter results
            limit: Maximum number of results to return
            filters: Additional metadata filters

        Returns:
            List of dictionaries containing:
            - id: Chunk ID
            - text: Chunk text
            - metadata: Chunk metadata
            - distance/score: Similarity score
        """
        pass

    @abstractmethod
    async def delete_document(self, document_id: str, tenant_id: int) -> bool:
        """
        Delete all chunks for a specific document.

        Args:
            document_id: Document ID to delete
            tenant_id: Tenant ID for security

        Returns:
            bool: True if successful, False otherwise
        """
        pass

    @abstractmethod
    async def get_document_chunks(
        self, document_id: str, tenant_id: int
    ) -> list[dict[str, Any]]:
        """
        Retrieve all chunks for a specific document.

        Args:
            document_id: Document ID
            tenant_id: Tenant ID for security

        Returns:
            List of chunk dictionaries
        """
        pass

    @abstractmethod
    async def update_metadata(
        self, chunk_ids: list[str], metadata: dict[str, Any], tenant_id: int
    ) -> bool:
        """
        Update metadata for specific chunks.

        Args:
            chunk_ids: List of chunk IDs to update
            metadata: New metadata to merge/replace
            tenant_id: Tenant ID for security

        Returns:
            bool: True if successful, False otherwise
        """
        pass

    @abstractmethod
    async def get_collection_stats(self, tenant_id: int) -> dict[str, Any]:
        """
        Get statistics about the vector store for a tenant.

        Args:
            tenant_id: Tenant ID

        Returns:
            Dictionary with stats like total_chunks, total_documents, etc.
        """
        pass

    @abstractmethod
    async def clear_tenant(self, tenant_id: int) -> bool:
        """
        Remove all vectors for a tenant (e.g., by dropping the tenant's collection).

        Args:
            tenant_id: Tenant identifier

        Returns:
            bool: True if successful, False otherwise
        """
        pass
