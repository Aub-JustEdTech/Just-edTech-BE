"""
ChromaDB implementation of vector store.
"""

import logging
import os
from typing import Any

try:
    import chromadb
    from chromadb.errors import InternalError
except ImportError:
    chromadb = None
    InternalError = None

from app.core.config import settings
from app.services.vector_store.base import VectorStore

logger = logging.getLogger(__name__)


class ChromaDBStore(VectorStore):
    """ChromaDB implementation of vector store"""

    def __init__(self, persist_directory: str = None):
        """
        Initialize ChromaDB client.

        Args:
            persist_directory: Directory to persist ChromaDB data
        """
        if chromadb is None:
            raise ImportError(
                "chromadb is not installed. Install it with: "
                "poetry install --with chroma"
            )

        # Get the directory path
        persist_dir = persist_directory or settings.CHROMA_PERSIST_DIR

        # Convert to absolute path to avoid any path resolution issues
        if not os.path.isabs(persist_dir):
            persist_dir = os.path.abspath(persist_dir)

        self.persist_directory = persist_dir

        # Ensure the directory exists with proper permissions
        try:
            os.makedirs(self.persist_directory, mode=0o755, exist_ok=True)
            logger.info(f"Ensured ChromaDB directory exists: {self.persist_directory}")
        except OSError as e:
            logger.error(
                f"Failed to create ChromaDB directory {self.persist_directory}: {e}"
            )
            raise

        # Use PersistentClient for ChromaDB 1.1.1+ (new API)
        try:
            self.client = chromadb.PersistentClient(path=self.persist_directory)
            logger.info(
                f"ChromaDB initialized with persist_directory: {self.persist_directory}"
            )
        except Exception as e:
            logger.error(
                f"Failed to initialize ChromaDB client with directory {self.persist_directory}: {e}"
            )
            raise

    def _get_collection_name(self, tenant_id: int) -> str:
        """Generate collection name for tenant"""
        return f"{settings.CHROMA_COLLECTION_PREFIX}_{tenant_id}_documents"

    def _get_or_create_collection(self, tenant_id: int):
        """Get or create a collection for the tenant"""
        collection_name = self._get_collection_name(tenant_id)
        try:
            collection = self.client.get_collection(name=collection_name)
            logger.debug(f"Using existing collection: {collection_name}")
        except Exception:
            collection = self.client.create_collection(
                name=collection_name,
                metadata={"tenant_id": str(tenant_id)},
            )
            logger.info(f"Created new collection: {collection_name}")
        return collection

    async def add_chunks(
        self,
        document_id: str,
        chunks: list[str],
        embeddings: list[list[float]],
        metadatas: list[dict[str, Any]],
    ) -> bool:
        """Store chunks in ChromaDB"""
        try:
            if not chunks or not embeddings or not metadatas:
                logger.warning("Empty chunks, embeddings, or metadatas provided")
                return False

            if len(chunks) != len(embeddings) != len(metadatas):
                logger.error("Mismatch in lengths of chunks, embeddings, and metadatas")
                return False

            # Get tenant_id from first metadata
            tenant_id = metadatas[0].get("tenant_id")
            if not tenant_id:
                logger.error("tenant_id not found in metadata")
                return False

            collection = self._get_or_create_collection(tenant_id)

            # Generate unique IDs for each chunk
            ids = [f"{document_id}_chunk_{i}" for i in range(len(chunks))]

            # Normalize metadata values for ChromaDB compatibility
            # ChromaDB requires metadata values to be str, int, or float
            normalized_metadatas = []
            for metadata in metadatas:
                normalized = {}
                for key, value in metadata.items():
                    # Convert values to ChromaDB-compatible types
                    if value is None:
                        continue  # Skip None values
                    elif isinstance(value, (str, int, float, bool)):
                        # Convert bool to int for ChromaDB compatibility
                        normalized[key] = (
                            int(value) if isinstance(value, bool) else value
                        )
                    elif isinstance(value, (list, dict)):
                        # ChromaDB doesn't support nested structures in metadata
                        # Convert to string representation
                        normalized[key] = str(value)
                    else:
                        normalized[key] = str(value)
                # Ensure tenant_id is consistent
                normalized["tenant_id"] = tenant_id
                normalized_metadatas.append(normalized)

            # Add to ChromaDB
            collection.add(
                ids=ids,
                documents=chunks,
                embeddings=embeddings,
                metadatas=normalized_metadatas,
            )

            logger.info(
                f"Added {len(chunks)} chunks for document {document_id} to tenant {tenant_id}"
            )
            return True

        except Exception as e:
            logger.error(f"Error adding chunks to ChromaDB: {e}", exc_info=True)
            return False

    async def search(
        self,
        query_embedding: list[float],
        tenant_id: int,
        limit: int = 5,
        filters: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Search for similar chunks"""
        try:
            collection = self._get_or_create_collection(tenant_id)
            collection_count: int | None = None

            # Build where clause for filtering
            where = filters if filters else None

            # Validate document IDs in filters exist in ChromaDB
            # We must validate because querying for a document ID that doesn't exist
            # causes an InternalError in ChromaDB (likely HNSW index issue).
            if where and "document_id" in where:
                doc_filter = where.get("document_id", {})
                if isinstance(doc_filter, dict) and "$in" in doc_filter:
                    doc_ids = doc_filter["$in"]
                    if doc_ids:
                        try:
                            valid_doc_ids = []
                            # Check each document individually to ensure it exists
                            # This avoids retrieving all metadata (slow) and avoids InternalError from query
                            for doc_id in doc_ids:
                                try:
                                    # Use limit=1 to just check existence efficiently
                                    # We check if *any* chunk exists for this document
                                    test_result = collection.get(
                                        where={"document_id": doc_id},
                                        limit=1,
                                        include=[] # Don't fetch data, just ID
                                    )
                                    if test_result.get("ids"):
                                        valid_doc_ids.append(doc_id)
                                except Exception as e:
                                    # If check fails, assume document is missing/invalid
                                    logger.debug(f"Document {doc_id} not found or error checking: {e}")
                                    pass
                            
                            if not valid_doc_ids:
                                logger.warning(
                                    f"None of the requested document IDs exist in ChromaDB for tenant {tenant_id}. "
                                    f"Requested: {doc_ids[:3]}... (total: {len(doc_ids)}). "
                                    f"Returning empty results to avoid InternalError."
                                )
                                return [] # Return empty immediately as fallback to "where=None" returns nothing anyway if collection issues exist
                            
                            elif len(valid_doc_ids) < len(doc_ids):
                                missing_ids = set(doc_ids) - set(valid_doc_ids)
                                logger.warning(
                                    f"Some document IDs not found in ChromaDB. "
                                    f"Requested: {len(doc_ids)}, Found: {len(valid_doc_ids)}. "
                                    f"Missing: {list(missing_ids)[:3]}... (total missing: {len(missing_ids)}). "
                                    f"Filtering query to only valid IDs."
                                )
                                where["document_id"] = {"$in": valid_doc_ids}
                            else:
                                logger.debug(
                                    f"All {len(doc_ids)} requested document IDs validated in ChromaDB"
                                )
                                
                        except Exception as validation_error:
                             logger.error(f"Error validating document IDs: {validation_error}", exc_info=True)
                             # If validation crashes, return empty to be safe
                             return []

            # Query ChromaDB

            # Query ChromaDB
            try:
                results = collection.query(
                    query_embeddings=[query_embedding], n_results=limit, where=where
                )
            except InternalError as query_error:
                # If query fails with filters due to ChromaDB internal error, try without filters as fallback
                if where is not None:
                    logger.warning(
                        f"ChromaDB InternalError with filters (likely invalid document IDs): {query_error}. "
                        f"Retrying without filters as fallback."
                    )
                    try:
                        results = collection.query(
                            query_embeddings=[query_embedding], n_results=limit, where=None
                        )
                    except Exception as fallback_error:
                        logger.error(
                            f"ChromaDB query failed even without filters: {fallback_error}",
                            exc_info=True,
                        )
                        return []
                else:
                    logger.error(
                        f"ChromaDB InternalError during query without filters: {query_error}",
                        exc_info=True,
                    )
                    return []
            except Exception as query_error:
                # For other errors, log and return empty
                logger.error(
                    f"Unexpected error during ChromaDB query: {query_error}",
                    exc_info=True,
                )
                return []

            # Format results
            chunks = []
            if results and "ids" in results and results["ids"]:
                result_ids = (
                    results["ids"][0]
                    if isinstance(results["ids"], list)
                    else results["ids"]
                )
                result_docs = (
                    results["documents"][0]
                    if isinstance(results["documents"], list)
                    else results["documents"]
                )
                result_metas = (
                    results["metadatas"][0]
                    if isinstance(results["metadatas"], list)
                    else results["metadatas"]
                )
                result_dists = (
                    results["distances"][0]
                    if isinstance(results["distances"], list)
                    else results["distances"]
                )

                if result_ids and len(result_ids) > 0:
                    logger.debug(
                        f"Processing {len(result_ids)} results from ChromaDB query "
                        f"(filters: {where})"
                    )
                    for i in range(len(result_ids)):
                        # Validate all arrays have the same length
                        if (
                            i < len(result_docs)
                            and i < len(result_metas)
                            and i < len(result_dists)
                        ):
                            distance = result_dists[i] if result_dists else 0.0
                            score = 1 - distance
                            chunk_metadata = result_metas[i] if result_metas else {}

                            chunks.append(
                                {
                                    "id": result_ids[i],
                                    "text": result_docs[i] if result_docs else "",
                                    "metadata": chunk_metadata,
                                    "distance": distance,
                                    "score": score,
                                }
                            )

                            # Log first few results for debugging
                            if i < 3:
                                logger.debug(
                                    f"Result {i+1}: id={result_ids[i][:50]}, "
                                    f"score={score:.4f}, distance={distance:.4f}, "
                                    f"doc_id={chunk_metadata.get('document_id', 'N/A')}"
                                )
                else:
                    logger.warning(
                        f"ChromaDB returned results structure but no IDs found. "
                        f"Results keys: {list(results.keys()) if results else 'None'}"
                    )
            else:
                log_collection_count: int | str | None = collection_count
                if log_collection_count is None:
                    try:
                        log_collection_count = collection.count()
                    except Exception as count_error:
                        log_collection_count = f"unknown (count failed: {count_error})"
                logger.warning(
                    f"ChromaDB query returned no results or empty structure. "
                    f"Collection count: {log_collection_count}, filters: {where}"
                )

            logger.info(f"Found {len(chunks)} similar chunks for tenant {tenant_id}")
            return chunks

        except Exception as e:
            logger.error(f"Error searching ChromaDB: {e}", exc_info=True)
            return []

    async def delete_document(self, document_id: str, tenant_id: int) -> bool:
        """Delete all chunks for a document"""
        try:
            collection = self._get_or_create_collection(tenant_id)

            try:
                chunk_data = collection.get(
                    where={"document_id": document_id},
                    include=[],
                )
            except Exception as e:
                logger.error(
                    f"Error fetching chunks for deletion (doc={document_id}, tenant={tenant_id}): {e}",
                    exc_info=True,
                )
                return False

            chunk_ids = chunk_data.get("ids", []) if chunk_data else []

            if chunk_ids:
                collection.delete(ids=chunk_ids)
            else:
                collection.delete(where={"document_id": document_id})

            verification = collection.get(
                where={"document_id": document_id},
                include=[],
            )
            if verification.get("ids"):
                logger.warning(
                    "ChromaDB delete verification found remaining chunks",
                    extra={
                        "document_id": document_id,
                        "tenant_id": tenant_id,
                        "remaining_ids": verification.get("ids"),
                    },
                )
                return False

            logger.info(f"Deleted document {document_id} from tenant {tenant_id}")
            return True

        except Exception as e:
            logger.error(f"Error deleting document from ChromaDB: {e}", exc_info=True)
            return False

    async def get_document_chunks(
        self, document_id: str, tenant_id: int
    ) -> list[dict[str, Any]]:
        """Get all chunks for a document"""
        try:
            collection = self._get_or_create_collection(tenant_id)

            # Get all chunks with matching document_id
            results = collection.get(
                where={"document_id": document_id}, include=["documents", "metadatas"]
            )

            # Format results
            chunks = []
            if results["ids"]:
                for i in range(len(results["ids"])):
                    chunks.append(
                        {
                            "id": results["ids"][i],
                            "text": results["documents"][i],
                            "metadata": results["metadatas"][i],
                        }
                    )

            logger.info(f"Retrieved {len(chunks)} chunks for document {document_id}")
            return chunks

        except Exception as e:
            logger.error(f"Error getting document chunks: {e}", exc_info=True)
            return []

    async def update_metadata(
        self, chunk_ids: list[str], metadata: dict[str, Any], tenant_id: int
    ) -> bool:
        """Update metadata for chunks"""
        try:
            collection = self._get_or_create_collection(tenant_id)

            # ChromaDB doesn't support direct metadata updates, so we need to get existing data
            # and update it
            for chunk_id in chunk_ids:
                results = collection.get(ids=[chunk_id], include=["metadatas"])
                if results["ids"]:
                    existing_metadata = results["metadatas"][0]
                    updated_metadata = {**existing_metadata, **metadata}
                    collection.update(ids=[chunk_id], metadatas=[updated_metadata])

            logger.info(f"Updated metadata for {len(chunk_ids)} chunks")
            return True

        except Exception as e:
            logger.error(f"Error updating metadata: {e}", exc_info=True)
            return False

    async def get_collection_stats(self, tenant_id: int) -> dict[str, Any]:
        """Get collection statistics"""
        try:
            collection = self._get_or_create_collection(tenant_id)
            count = collection.count()

            # Get unique document IDs
            results = collection.get(include=["metadatas"])
            document_ids = set()
            if results["metadatas"]:
                document_ids = {
                    meta.get("document_id") for meta in results["metadatas"]
                }

            stats = {
                "total_chunks": count,
                "total_documents": len(document_ids),
                "collection_name": self._get_collection_name(tenant_id),
            }

            logger.info(f"Collection stats for tenant {tenant_id}: {stats}")
            return stats

        except Exception as e:
            logger.error(f"Error getting collection stats: {e}", exc_info=True)
            return {
                "total_chunks": 0,
                "total_documents": 0,
                "error": str(e),
            }

    async def clear_tenant(self, tenant_id: int) -> bool:
        """Delete the entire collection for a tenant."""
        try:
            collection_name = self._get_collection_name(tenant_id)
            try:
                # Ensure it exists first to avoid noisy errors
                self.client.get_collection(name=collection_name)
            except Exception:
                logger.info(
                    f"Collection {collection_name} does not exist; nothing to clear"
                )
                return True

            self.client.delete_collection(name=collection_name)
            logger.info(
                f"Deleted ChromaDB collection for tenant {tenant_id} ({collection_name})"
            )
            return True
        except Exception as e:
            logger.error(
                f"Failed to clear tenant {tenant_id} collection: {e}", exc_info=True
            )
            return False
