"""
Qdrant implementation of vector store.
"""

import asyncio
import logging
import uuid
from typing import Any

import httpx
from qdrant_client import QdrantClient
from qdrant_client.http import models
from qdrant_client.http.exceptions import ResponseHandlingException, UnexpectedResponse

from app.core.config import settings
from app.services.vector_store.base import VectorStore

logger = logging.getLogger(__name__)


class QdrantStore(VectorStore):
    """Qdrant implementation of vector store"""

    def __init__(self, url: str = None):
        """
        Initialize Qdrant client.

        Args:
            url: Qdrant server URL (defaults to settings.QDRANT_URL)
        """
        self.url = url or settings.QDRANT_URL

        try:
            # Disable version check for compatibility with older servers
            self.client = QdrantClient(
                url=self.url, check_compatibility=False
            )
            logger.info(f"Qdrant initialized with URL: {self.url}")
        except Exception as e:
            logger.error(f"Failed to initialize Qdrant client with URL {self.url}: {e}")
            raise

    def _get_collection_name(self, tenant_id: int) -> str:
        """Generate collection name for tenant"""
        return f"{settings.QDRANT_COLLECTION_PREFIX}_{tenant_id}_documents"

    def _get_image_collection_name(self, tenant_id: int) -> str:
        """Generate collection name for image captions"""
        return f"{settings.QDRANT_COLLECTION_PREFIX}_{tenant_id}_images"

    def _get_summaries_collection_name(self, tenant_id: int) -> str:
        """Generate collection name for document summary embeddings"""
        return f"{settings.QDRANT_COLLECTION_PREFIX}_{tenant_id}_summaries"

    @staticmethod
    def _is_valid_uuid(uuid_string: str) -> bool:
        """Check if a string is a valid UUID"""
        try:
            uuid.UUID(uuid_string)
            return True
        except (ValueError, AttributeError, TypeError):
            return False

    def _insert_point_via_rest(self, collection_name: str, point: models.PointStruct):
        """
        Insert a single point via REST API as last resort for Qdrant 1.7.0.
        
        Note: This method may not work with Qdrant 1.7.0 due to API format differences.
        The recommended solution is to upgrade Qdrant server to 1.8.0+.
        
        Args:
            collection_name: Name of the collection
            point: PointStruct object to insert
        """
        url = f"{self.url}/collections/{collection_name}/points"
        
        # Convert PointStruct to dict format
        # Qdrant 1.7.0 may require specific formatting
        point_dict = {
            "id": point.id,
            "vector": point.vector,
        }
        
        # Only include payload if it's not empty
        if point.payload:
            point_dict["payload"] = point.payload
        
        # Qdrant REST API expects points array
        payload = {"points": [point_dict]}
        
        try:
            response = httpx.put(url, json=payload, params={"wait": "true"}, timeout=30.0)
            response.raise_for_status()
        except httpx.HTTPStatusError as e:
            error_msg = f"Failed to insert point via REST API: {e.response.status_code}"
            if e.response.content:
                try:
                    error_detail = e.response.json()
                    error_msg += f" - {error_detail}"
                except:
                    error_msg += f" - {e.response.text}"
            
            logger.error(
                f"REST API insert failed for collection {collection_name}. "
                f"This is likely due to Qdrant 1.7.0 incompatibility. "
                f"Error: {error_msg}. "
                f"Recommendation: Upgrade Qdrant server to 1.8.0+"
            )
            raise

    def _upsert_points_individually(self, collection_name: str, points: list[dict]):
        """
        Upsert points individually using the client's set_payload and upload_records
        for compatibility with Qdrant 1.7.0.
        
        Args:
            collection_name: Name of the collection
            points: List of point dictionaries with id, vector, and payload
        """
        # For Qdrant 1.7.0 compatibility, we need to use PointStruct objects
        # and insert them one at a time or in small batches
        point_structs = []
        for point in points:
            point_structs.append(
                models.PointStruct(
                    id=point["id"],
                    vector=point["vector"],
                    payload=point["payload"],
                )
            )
        
        # Insert points in batches of 10 for better performance
        batch_size = 10
        for i in range(0, len(point_structs), batch_size):
            batch = point_structs[i : i + batch_size]
            # Use the client's internal method or try individual inserts
            for point_struct in batch:
                try:
                    # Try to insert individual point
                    self.client.upsert(
                        collection_name=collection_name,
                        points=[point_struct],
                        wait=True,
                    )
                except Exception as batch_error:
                    # If individual insert also fails, log and continue
                    logger.warning(
                        f"Failed to insert point {point_struct.id}: {batch_error}"
                    )
                    # Try using REST API directly as last resort
                    self._insert_point_via_rest(collection_name, point_struct)

    async def _get_or_create_collection(self, tenant_id: int, vector_size: int = None):
        """Get or create a collection for the tenant"""
        collection_name = self._get_collection_name(tenant_id)

        try:
            try:
                await asyncio.to_thread(self.client.get_collection, collection_name)
                logger.debug(f"Using existing Qdrant collection: {collection_name}")
            except Exception:
                if vector_size is None:
                    vector_size = 1536

                await asyncio.to_thread(
                    self.client.create_collection,
                    collection_name=collection_name,
                    vectors_config=models.VectorParams(
                        size=vector_size,
                        distance=models.Distance.COSINE,
                    ),
                )
                logger.info(f"Created new Qdrant collection: {collection_name}")

            return collection_name
        except Exception as e:
            logger.error(f"Error getting/creating Qdrant collection: {e}", exc_info=True)
            raise

    async def add_chunks(
        self,
        document_id: str,
        chunks: list[str],
        embeddings: list[list[float]],
        metadatas: list[dict[str, Any]],
    ) -> bool:
        """Store chunks in Qdrant"""
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

            vector_size = len(embeddings[0]) if embeddings else 1536
            collection_name = await self._get_or_create_collection(tenant_id, vector_size)

            # Prepare points for Qdrant
            # Use dict format for better compatibility with older server versions
            points = []
            for i, (chunk, embedding, metadata) in enumerate(
                zip(chunks, embeddings, metadatas)
            ):
                # Generate a proper UUID for the point ID
                # Qdrant only accepts unsigned integers or valid UUIDs
                # We generate a unique UUID4 for each chunk
                point_id = str(uuid.uuid4())

                # Normalize metadata for Qdrant (Qdrant supports more types than ChromaDB)
                # Qdrant payload can contain: str, int, float, bool, list, dict
                normalized_metadata = {}
                for key, value in metadata.items():
                    if value is None:
                        continue  # Skip None values
                    elif isinstance(value, (str, int, float, bool)):
                        normalized_metadata[key] = value
                    elif isinstance(value, (list, dict)):
                        # Qdrant supports nested structures
                        normalized_metadata[key] = value
                    else:
                        normalized_metadata[key] = str(value)

                # Ensure tenant_id is consistent
                normalized_metadata["tenant_id"] = tenant_id
                normalized_metadata["document_id"] = document_id
                normalized_metadata["text"] = chunk  # Store text in payload
                normalized_metadata["chunk_index"] = i  # Store index for reference

                # Use dict format for compatibility with older Qdrant server versions
                points.append(
                    {
                        "id": point_id,
                        "vector": embedding,
                        "payload": normalized_metadata,
                    }
                )

            try:
                await asyncio.to_thread(
                    self.client.upsert,
                    collection_name=collection_name, points=points, wait=True,
                )
            except UnexpectedResponse as e:
                if "PointInsertOperations" in str(e):
                    logger.debug(
                        "Standard upsert failed due to server version incompatibility, "
                        "using individual point inserts via REST API"
                    )
                    await asyncio.to_thread(
                        self._upsert_points_individually, collection_name, points,
                    )
                else:
                    raise

            logger.info(
                f"Added {len(chunks)} chunks for document {document_id} to tenant {tenant_id}"
            )
            return True

        except Exception as e:
            logger.error(f"Error adding chunks to Qdrant: {e}", exc_info=True)
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
            collection_name = self._get_collection_name(tenant_id)

            # Build query filter
            # Always include tenant_id filter for security
            must_conditions = [
                models.FieldCondition(
                    key="tenant_id",
                    match=models.MatchValue(value=tenant_id),
                )
            ]

            # Add document_id filter if provided
            if filters and "document_id" in filters:
                doc_filter = filters["document_id"]
                if isinstance(doc_filter, dict) and "$in" in doc_filter:
                    doc_ids = doc_filter["$in"]
                    if doc_ids:
                        must_conditions.append(
                            models.FieldCondition(
                                key="document_id",
                                match=models.MatchAny(any=doc_ids),
                            )
                        )
                elif isinstance(doc_filter, str):
                    must_conditions.append(
                        models.FieldCondition(
                            key="document_id",
                            match=models.MatchValue(value=doc_filter),
                        )
                    )

            # Add document_type filter if provided (e.g. for search_tables)
            if filters and "document_type" in filters:
                type_filter = filters["document_type"]
                if isinstance(type_filter, list) and type_filter:
                    must_conditions.append(
                        models.FieldCondition(
                            key="document_type",
                            match=models.MatchAny(any=type_filter),
                        )
                    )
                elif isinstance(type_filter, str):
                    must_conditions.append(
                        models.FieldCondition(
                            key="document_type",
                            match=models.MatchValue(value=type_filter),
                        )
                    )

            # Create filter with all conditions
            query_filter = models.Filter(must=must_conditions)

            try:
                try:
                    await asyncio.to_thread(self.client.get_collection, collection_name)
                except Exception:
                    logger.debug(
                        f"Qdrant collection {collection_name} does not exist, returning empty results"
                    )
                    return []

                query_response = await asyncio.to_thread(
                    self.client.query_points,
                    collection_name=collection_name,
                    query=query_embedding,
                    query_filter=query_filter,
                    limit=limit,
                    with_payload=True,
                )
            except (ResponseHandlingException, UnexpectedResponse) as e:
                logger.warning(
                    f"Qdrant search error for collection {collection_name}: {e}"
                )
                return []

            # Format results
            chunks = []
            # QueryResponse has a .points attribute with list of ScoredPoint objects
            for scored_point in query_response.points:
                payload = scored_point.payload or {}
                score = scored_point.score
                point_id = scored_point.id
                
                # Qdrant score is similarity (higher is better for cosine)
                # Convert to distance (lower is better)
                distance = 1 - score if score <= 1.0 else 0

                chunks.append(
                    {
                        "id": str(point_id),
                        "text": payload.get("text", ""),
                        "metadata": {k: v for k, v in payload.items() if k != "text"},
                        "distance": distance,
                        "score": score,
                    }
                )

            logger.info(f"Found {len(chunks)} similar chunks for tenant {tenant_id}")
            return chunks

        except Exception as e:
            logger.error(f"Error searching Qdrant: {e}", exc_info=True)
            return []

    async def delete_document(self, document_id: str, tenant_id: int) -> bool:
        """Delete all chunks for a document"""
        try:
            collection_name = self._get_collection_name(tenant_id)

            await asyncio.to_thread(
                self.client.delete,
                collection_name=collection_name,
                points_selector=models.FilterSelector(
                    filter=models.Filter(
                        must=[
                            models.FieldCondition(
                                key="document_id",
                                match=models.MatchValue(value=document_id),
                            ),
                            models.FieldCondition(
                                key="tenant_id",
                                match=models.MatchValue(value=tenant_id),
                            ),
                        ]
                    )
                ),
            )

            logger.info(f"Deleted document {document_id} from tenant {tenant_id}")
            return True

        except Exception as e:
            logger.error(f"Error deleting document from Qdrant: {e}", exc_info=True)
            return False

    async def get_document_chunks(
        self, document_id: str, tenant_id: int
    ) -> list[dict[str, Any]]:
        """Get all chunks for a document"""
        try:
            collection_name = self._get_collection_name(tenant_id)

            scroll_results = await asyncio.to_thread(
                self.client.scroll,
                collection_name=collection_name,
                scroll_filter=models.Filter(
                    must=[
                        models.FieldCondition(
                            key="document_id",
                            match=models.MatchValue(value=document_id),
                        ),
                        models.FieldCondition(
                            key="tenant_id",
                            match=models.MatchValue(value=tenant_id),
                        ),
                    ]
                ),
                limit=10000,
                with_payload=True,
                with_vectors=False,
            )

            # Format results
            chunks = []
            for point in scroll_results[0]:  # scroll_results is (points, next_page_offset)
                payload = point.payload or {}
                chunks.append(
                    {
                        "id": str(point.id),
                        "text": payload.get("text", ""),
                        "metadata": {k: v for k, v in payload.items() if k != "text"},
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
            collection_name = self._get_collection_name(tenant_id)

            points = await asyncio.to_thread(
                self.client.retrieve,
                collection_name=collection_name,
                ids=chunk_ids,
                with_payload=True,
                with_vectors=False,
            )

            for point in points:
                existing_payload = point.payload or {}
                updated_payload = {**existing_payload, **metadata}
                updated_payload["tenant_id"] = tenant_id

                await asyncio.to_thread(
                    self.client.set_payload,
                    collection_name=collection_name,
                    payload=updated_payload,
                    points=[point.id],
                )

            logger.info(f"Updated metadata for {len(chunk_ids)} chunks")
            return True

        except Exception as e:
            logger.error(f"Error updating metadata: {e}", exc_info=True)
            return False

    async def add_image_captions(
        self,
        document_id: str,
        image_captions: list[str],
        embeddings: list[list[float]],
        metadatas: list[dict[str, Any]],
    ) -> bool:
        """Store image captions in Qdrant"""
        try:
            if not image_captions or not embeddings or not metadatas:
                logger.warning("Empty image captions, embeddings, or metadatas provided")
                return False

            if len(image_captions) != len(embeddings) != len(metadatas):
                logger.error("Mismatch in lengths of image captions, embeddings, and metadatas")
                return False

            # Get tenant_id from first metadata
            tenant_id = metadatas[0].get("tenant_id")
            if not tenant_id:
                logger.error("tenant_id not found in metadata")
                return False

            # Get vector size from first embedding
            vector_size = len(embeddings[0]) if embeddings else 1536
            collection_name = self._get_image_collection_name(tenant_id)
            
            try:
                await asyncio.to_thread(self.client.get_collection, collection_name)
            except Exception:
                await asyncio.to_thread(
                    self.client.create_collection,
                    collection_name=collection_name,
                    vectors_config=models.VectorParams(
                        size=vector_size,
                        distance=models.Distance.COSINE,
                    ),
                )
                logger.info(f"Created new Qdrant image collection: {collection_name}")

            # Prepare points for Qdrant
            points = []
            for i, (combined_text, embedding, metadata) in enumerate(
                zip(image_captions, embeddings, metadatas)
            ):
                # Generate a proper UUID for the point ID
                # Qdrant only accepts unsigned integers or valid UUIDs
                # We generate a unique UUID4 for each image
                point_id = str(uuid.uuid4())

                # Normalize metadata for Qdrant
                normalized_metadata = {}
                for key, value in metadata.items():
                    if value is None:
                        continue
                    elif isinstance(value, (str, int, float, bool)):
                        normalized_metadata[key] = value
                    elif isinstance(value, (list, dict)):
                        normalized_metadata[key] = value
                    else:
                        normalized_metadata[key] = str(value)

                # Ensure tenant_id is consistent
                normalized_metadata["tenant_id"] = tenant_id
                normalized_metadata["document_id"] = document_id
                
                # Store the combined text that was actually embedded
                # This is what was passed as image_captions (combined_text)
                normalized_metadata["combined_text"] = combined_text
                
                # Store original caption if available in metadata, otherwise use combined_text
                # The caption field should be in metadata from _process_images()
                if "caption" not in normalized_metadata:
                    # Fallback: if caption not in metadata, use combined_text
                    normalized_metadata["caption"] = combined_text
                
                # Store surrounding text context if available
                # These should be in metadata from _process_images()
                # (They're already included via the metadata loop above, but we ensure they're present)
                
                normalized_metadata["type"] = "image"  # Mark as image type
                normalized_metadata["image_index"] = i  # Store index for reference

                points.append(
                    {
                        "id": point_id,
                        "vector": embedding,
                        "payload": normalized_metadata,
                    }
                )

            try:
                await asyncio.to_thread(
                    self.client.upsert,
                    collection_name=collection_name, points=points, wait=True,
                )
            except UnexpectedResponse as e:
                if "PointInsertOperations" in str(e):
                    logger.debug(
                        "Standard upsert failed, using individual point inserts"
                    )
                    await asyncio.to_thread(
                        self._upsert_points_individually, collection_name, points,
                    )
                else:
                    raise

            logger.info(
                f"Added {len(image_captions)} image captions for document {document_id} to tenant {tenant_id}"
            )
            return True

        except Exception as e:
            logger.error(f"Error adding image captions to Qdrant: {e}", exc_info=True)
            return False

    async def search_images(
        self,
        query_embedding: list[float],
        tenant_id: int,
        limit: int = 1,
        filters: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Search for similar image captions"""
        try:
            collection_name = self._get_image_collection_name(tenant_id)

            # Build query filter
            must_conditions = [
                models.FieldCondition(
                    key="tenant_id",
                    match=models.MatchValue(value=tenant_id),
                ),
                models.FieldCondition(
                    key="type",
                    match=models.MatchValue(value="image"),
                ),
            ]

            # Add document_id filter if provided
            if filters and "document_id" in filters:
                doc_filter = filters["document_id"]
                if isinstance(doc_filter, dict) and "$in" in doc_filter:
                    doc_ids = doc_filter["$in"]
                    if doc_ids:
                        must_conditions.append(
                            models.FieldCondition(
                                key="document_id",
                                match=models.MatchAny(any=doc_ids),
                            )
                        )
                elif isinstance(doc_filter, str):
                    must_conditions.append(
                        models.FieldCondition(
                            key="document_id",
                            match=models.MatchValue(value=doc_filter),
                        )
                    )

            # Create filter with all conditions
            query_filter = models.Filter(must=must_conditions)

            try:
                try:
                    await asyncio.to_thread(self.client.get_collection, collection_name)
                except Exception:
                    logger.debug(
                        f"Qdrant image collection {collection_name} does not exist, returning empty results"
                    )
                    return []

                query_response = await asyncio.to_thread(
                    self.client.query_points,
                    collection_name=collection_name,
                    query=query_embedding,
                    query_filter=query_filter,
                    limit=limit,
                    with_payload=True,
                )
            except (ResponseHandlingException, UnexpectedResponse) as e:
                logger.warning(
                    f"Qdrant image search error for collection {collection_name}: {e}"
                )
                return []

            # Format results
            images = []
            # QueryResponse has a .points attribute with list of ScoredPoint objects
            for scored_point in query_response.points:
                payload = scored_point.payload or {}
                score = scored_point.score
                point_id = scored_point.id
                
                # Qdrant score is similarity (higher is better for cosine)
                # Convert to distance (lower is better)
                distance = 1 - score if score <= 1.0 else 0

                images.append(
                    {
                        "id": str(point_id),
                        "caption": payload.get("caption", ""),
                        "metadata": {k: v for k, v in payload.items() if k != "caption"},
                        "distance": distance,
                        "score": score,
                    }
                )

            logger.info(f"Found {len(images)} similar images for tenant {tenant_id}")
            return images

        except Exception as e:
            logger.error(f"Error searching images in Qdrant: {e}", exc_info=True)
            return []

    async def get_collection_stats(self, tenant_id: int) -> dict[str, Any]:
        """Get collection statistics"""
        try:
            collection_name = self._get_collection_name(tenant_id)

            try:
                collection_info = await asyncio.to_thread(
                    self.client.get_collection, collection_name,
                )
                total_chunks = collection_info.points_count

                scroll_results = await asyncio.to_thread(
                    self.client.scroll,
                    collection_name=collection_name,
                    limit=10000,
                    with_payload=True,
                    with_vectors=False,
                )

                document_ids = set()
                for point in scroll_results[0]:
                    payload = point.payload or {}
                    doc_id = payload.get("document_id")
                    if doc_id:
                        document_ids.add(doc_id)

                stats = {
                    "total_chunks": total_chunks,
                    "total_documents": len(document_ids),
                    "collection_name": collection_name,
                }

                logger.info(f"Collection stats for tenant {tenant_id}: {stats}")
                return stats

            except Exception as e:
                # Collection doesn't exist
                logger.debug(f"Collection {collection_name} does not exist: {e}")
                return {
                    "total_chunks": 0,
                    "total_documents": 0,
                    "collection_name": collection_name,
                }

        except Exception as e:
            logger.error(f"Error getting collection stats: {e}", exc_info=True)
            return {
                "total_chunks": 0,
                "total_documents": 0,
                "error": str(e),
            }

    async def add_document_summary(
        self,
        document_id: int,
        doc_uuid: str,
        document_name: str,
        tenant_id: int,
        summary_text: str,
        embedding: list[float],
        metadata: dict[str, Any],
    ) -> bool:
        """
        Store a single document-level summary embedding in the summaries
        collection (`tenant_{id}_summaries`).
        """
        try:
            collection_name = self._get_summaries_collection_name(tenant_id)
            vector_size = len(embedding)

            try:
                await asyncio.to_thread(self.client.get_collection, collection_name)
            except Exception:
                await asyncio.to_thread(
                    self.client.create_collection,
                    collection_name=collection_name,
                    vectors_config=models.VectorParams(
                        size=vector_size,
                        distance=models.Distance.COSINE,
                    ),
                )
                logger.info(
                    f"Created Qdrant summaries collection: {collection_name}"
                )

            payload: dict[str, Any] = {
                "document_id": document_id,
                "doc_uuid": doc_uuid,
                "document_name": document_name,
                "tenant_id": tenant_id,
                "summary_text": summary_text,
                **{k: v for k, v in metadata.items() if v is not None},
            }

            point_id = str(uuid.uuid4())
            try:
                await asyncio.to_thread(
                    self.client.upsert,
                    collection_name=collection_name,
                    points=[
                        models.PointStruct(
                            id=point_id,
                            vector=embedding,
                            payload=payload,
                        )
                    ],
                    wait=True,
                )
            except UnexpectedResponse as exc:
                if "PointInsertOperations" in str(exc):
                    await asyncio.to_thread(
                        self._upsert_points_individually,
                        collection_name,
                        [{"id": point_id, "vector": embedding, "payload": payload}],
                    )
                else:
                    raise

            logger.info(
                f"Indexed summary for document {document_id} in {collection_name}"
            )
            return True
        except Exception as exc:
            logger.error(
                f"Error adding document summary to Qdrant: {exc}", exc_info=True
            )
            return False

    async def search_summaries(
        self,
        query_embedding: list[float],
        tenant_id: int,
        limit: int = 5,
    ) -> list[dict[str, Any]]:
        """
        Search the summaries collection and return document-level results.

        Each result contains: document_id (int), doc_uuid, document_name,
        doc_category, doc_date_range, summary, score.
        """
        try:
            collection_name = self._get_summaries_collection_name(tenant_id)

            try:
                await asyncio.to_thread(self.client.get_collection, collection_name)
            except Exception:
                logger.debug(
                    f"Summaries collection {collection_name} does not exist."
                )
                return []

            query_filter = models.Filter(
                must=[
                    models.FieldCondition(
                        key="tenant_id",
                        match=models.MatchValue(value=tenant_id),
                    )
                ]
            )

            try:
                query_response = await asyncio.to_thread(
                    self.client.query_points,
                    collection_name=collection_name,
                    query=query_embedding,
                    query_filter=query_filter,
                    limit=limit,
                    with_payload=True,
                )
            except (ResponseHandlingException, UnexpectedResponse) as exc:
                logger.warning(
                    f"Qdrant summaries search error for {collection_name}: {exc}"
                )
                return []

            results = []
            for scored_point in query_response.points:
                payload = scored_point.payload or {}
                results.append(
                    {
                        "document_id": payload.get("document_id"),
                        "doc_uuid": payload.get("doc_uuid"),
                        "document_name": payload.get("document_name"),
                        "doc_category": payload.get("doc_category"),
                        "doc_date_range": payload.get("doc_date_range"),
                        "summary": payload.get("summary", ""),
                        "score": scored_point.score,
                    }
                )

            logger.info(
                f"Found {len(results)} summary matches for tenant {tenant_id}"
            )
            return results
        except Exception as exc:
            logger.error(
                f"Error searching summaries in Qdrant: {exc}", exc_info=True
            )
            return []

    async def delete_document_summary(
        self, document_id: int, tenant_id: int
    ) -> bool:
        """Remove a document's summary embedding from the summaries collection."""
        try:
            collection_name = self._get_summaries_collection_name(tenant_id)
            await asyncio.to_thread(
                self.client.delete,
                collection_name=collection_name,
                points_selector=models.FilterSelector(
                    filter=models.Filter(
                        must=[
                            models.FieldCondition(
                                key="document_id",
                                match=models.MatchValue(value=document_id),
                            ),
                            models.FieldCondition(
                                key="tenant_id",
                                match=models.MatchValue(value=tenant_id),
                            ),
                        ]
                    )
                ),
            )
            logger.info(
                f"Deleted summary embedding for document {document_id} "
                f"from {collection_name}"
            )
            return True
        except Exception as exc:
            logger.error(
                f"Error deleting document summary from Qdrant: {exc}", exc_info=True
            )
            return False

    async def clear_tenant(self, tenant_id: int) -> bool:
        """Delete the entire collection for a tenant."""
        try:
            collection_name = self._get_collection_name(tenant_id)

            try:
                collections = await asyncio.to_thread(self.client.get_collections)
                existing_names = [col.name for col in collections.collections]

                if collection_name in existing_names:
                    await asyncio.to_thread(
                        self.client.delete_collection, collection_name,
                    )
                    logger.info(
                        f"Deleted Qdrant collection for tenant {tenant_id} ({collection_name})"
                    )
                else:
                    logger.info(
                        f"Collection {collection_name} does not exist; nothing to clear"
                    )
                return True

            except Exception as e:
                logger.error(
                    f"Failed to clear tenant {tenant_id} collection: {e}", exc_info=True
                )
                return False

        except Exception as e:
            logger.error(
                f"Error clearing tenant {tenant_id}: {e}", exc_info=True
            )
            return False
