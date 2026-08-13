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

    async def filter_chunks(
        self,
        tenant_id: int,
        *,
        must_match: dict[str, Any] | None = None,
        must_match_any: dict[str, list] | None = None,
        nested_match_any: dict[str, list] | None = None,
        range_match: dict[str, dict[str, str]] | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """
        Scroll chunks by payload filter (no vector query).

        Used by the heatmap citations path to retrieve all chunks for a
        given (source_id, topic) without computing an embedding.

        Args:
            tenant_id: Tenant scope.
            must_match: {field: value} — all must equal the value.
            must_match_any: {field: [values]} — field must contain any.
            nested_match_any: {nested_array_field: [values]} — applies a
                nested-object filter where the array field contains an
                object whose values match. Used for `topic_tags` (a list
                of `{category, subtopic}` objects). Only one nested value
                per field is supported here; pass multiple keys for
                multiple nested fields.
            range_match: {field: {"gte": iso, "lte": iso}} — field must
                fall within the given datetime bounds. Requires a
                DATETIME payload index on the field. Used by the heatmap
                engine's custom date-range filter.
            limit: Max results.

        Returns:
            List of {id, text, metadata, score} dicts (score = 1.0
            for payload-only matches).
        """
        # Default implementation: not supported. Subclasses override.
        return []

    async def count_chunks(
        self,
        tenant_id: int,
        *,
        must_match: dict[str, Any] | None = None,
        must_match_any: dict[str, list] | None = None,
        nested_match_any: dict[str, list] | None = None,
        range_match: dict[str, dict[str, str]] | None = None,
    ) -> int:
        """
        Count chunks matching a payload filter (no vector query, no scroll).

        Used by the Heatmap Generation Engine to count chunk instances per
        district without materializing the chunks. Implementations should
        prefer the vector store's native count API where available.

        Args:
            tenant_id: Tenant scope.
            must_match: {field: value} — all must equal the value.
            must_match_any: {field: [values]} — field must contain any.
            nested_match_any: same semantics as `filter_chunks`.
            range_match: same semantics as `filter_chunks`.

        Returns:
            Number of matching chunks (0 on missing collection or error).
        """
        # Default implementation: fall back to filter_chunks + len.
        chunks = await self.filter_chunks(
            tenant_id,
            must_match=must_match,
            must_match_any=must_match_any,
            nested_match_any=nested_match_any,
            range_match=range_match,
            limit=100_000,
        )
        return len(chunks)

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
