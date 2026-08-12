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
                url=self.url,
                check_compatibility=False,
                timeout=settings.QDRANT_CLIENT_TIMEOUT_SECONDS,
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

            # Ensure payload indexes exist for commonly-filtered keys.
            # These make searches on school-scraper metadata efficient.
            # Idempotent: creating an index that already exists is a no-op.
            await self._ensure_payload_indexes(collection_name)

            return collection_name
        except Exception as e:
            logger.error(f"Error getting/creating Qdrant collection: {e}", exc_info=True)
            raise

    async def _ensure_payload_indexes(self, collection_name: str) -> None:
        """Create payload indexes for school-scraper filterable fields.

        Safe to call repeatedly; Qdrant treats duplicate index creation as
        a no-op. Wrapped per-field so one missing index doesn't block others.
        """
        # keyword fields
        keyword_fields = [
            "tenant_id",
            "document_id",
            "document_type",
            "school_org_code",
            "school_name",
            "school_id",
            "district_type",
            "document_type",
            "source_page_url",
            "source_media_url",
            "scrape_run_id",
        ]
        for field in keyword_fields:
            try:
                await asyncio.to_thread(
                    self.client.create_payload_index,
                    collection_name=collection_name,
                    field_name=field,
                    field_schema=models.PayloadSchemaType.KEYWORD,
                )
            except Exception:
                # Already exists or field not present yet; either way, ignore.
                pass

        # datetime / date-like fields stored as strings — index as keyword
        # since we store ISO strings, not Qdrant datetime values.
        for field in ("meeting_date", "scraped_at"):
            try:
                await asyncio.to_thread(
                    self.client.create_payload_index,
                    collection_name=collection_name,
                    field_name=field,
                    field_schema=models.PayloadSchemaType.KEYWORD,
                )
            except Exception:
                pass

        # Heatmap-ingest classification fields. These are written by the
        # batch classifier after a Batch API run completes (see
        # app/services/heatmap_ingest/batch_classifier.py).
        for field in (
            "entity_type",
            "classified",
        ):
            try:
                await asyncio.to_thread(
                    self.client.create_payload_index,
                    collection_name=collection_name,
                    field_name=field,
                    field_schema=models.PayloadSchemaType.KEYWORD,
                )
            except Exception:
                pass

        # Multi-label array fields (topics, action_types, subtopics) —
        # Qdrant indexes array payloads with the same KEYWORD schema so
        # `topics contains 'sex_education'` queries are fast.
        for field in ("topics", "action_types", "subtopics"):
            try:
                await asyncio.to_thread(
                    self.client.create_payload_index,
                    collection_name=collection_name,
                    field_name=field,
                    field_schema=models.PayloadSchemaType.KEYWORD,
                )
            except Exception:
                pass

        # Heatmap V1 metadata fields (spec: Heatmap Ingest Metadata v1).
        # Doc-level denorm + per-chunk keyword flags + placeholders for the
        # batch classifier output. Indexed as KEYWORD so payload pre-filter
        # queries on state / district_name / school_year / etc. are fast.
        v1_keyword_fields = (
            "state",
            "district_name",
            "school_year",
            "quarter_month",
            "meeting_doc_type",
            "meeting_body",
            "document_quality",
            "action_stage",
            "keyword_flags",
            "topic_tags",
            "speakers",
        )
        for field in v1_keyword_fields:
            try:
                await asyncio.to_thread(
                    self.client.create_payload_index,
                    collection_name=collection_name,
                    field_name=field,
                    field_schema=models.PayloadSchemaType.KEYWORD,
                )
            except Exception:
                pass

    async def add_chunks(
        self,
        document_id: str,
        chunks: list[str],
        embeddings: list[list[float]],
        metadatas: list[dict[str, Any]],
    ) -> bool:
        """Store chunks in Qdrant"""
        point_ids = await self.add_chunks_returning_ids(
            document_id, chunks, embeddings, metadatas
        )
        return bool(point_ids)

    async def add_chunks_returning_ids(
        self,
        document_id: str,
        chunks: list[str],
        embeddings: list[list[float]],
        metadatas: list[dict[str, Any]],
    ) -> list[str]:
        """
        Store chunks in Qdrant and return the list of generated point IDs.

        Used by the heatmap ingest pipeline (step6_accumulate_batch) which
        needs the per-chunk point IDs to write pending_classifications rows
        and later apply batch results via set_payload.

        Upserts are batched (QDRANT_UPSERT_BATCH_SIZE points/call) and a
        failed batch is raised rather than swallowed into an empty result:
        a silent [] here previously got reported as pipeline success with
        nothing actually stored.
        """
        if not chunks or not embeddings or not metadatas:
            logger.warning("Empty chunks, embeddings, or metadatas provided")
            return []

        if len(chunks) != len(embeddings) != len(metadatas):
            logger.error("Mismatch in lengths of chunks, embeddings, and metadatas")
            return []

        # Get tenant_id from first metadata
        tenant_id = metadatas[0].get("tenant_id")
        if not tenant_id:
            logger.error("tenant_id not found in metadata")
            return []

        vector_size = len(embeddings[0]) if embeddings else 1536
        collection_name = await self._get_or_create_collection(tenant_id, vector_size)

        # Prepare points for Qdrant
        # Use dict format for better compatibility with older server versions
        points = []
        point_ids: list[str] = []
        for i, (chunk, embedding, metadata) in enumerate(
            zip(chunks, embeddings, metadatas)
        ):
            # Generate a proper UUID for the point ID
            # Qdrant only accepts unsigned integers or valid UUIDs
            # We generate a unique UUID4 for each chunk
            point_id = str(uuid.uuid4())
            point_ids.append(point_id)

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

        batch_size = settings.QDRANT_UPSERT_BATCH_SIZE
        for start in range(0, len(points), batch_size):
            batch = points[start : start + batch_size]
            try:
                await asyncio.to_thread(
                    self.client.upsert,
                    collection_name=collection_name,
                    points=batch,
                    wait=True,
                )
            except UnexpectedResponse as e:
                if "PointInsertOperations" in str(e):
                    logger.debug(
                        "Standard upsert failed due to server version incompatibility, "
                        "using individual point inserts via REST API"
                    )
                    await asyncio.to_thread(
                        self._upsert_points_individually,
                        collection_name,
                        batch,
                    )
                else:
                    raise

        logger.info(
            f"Added {len(chunks)} chunks for document {document_id} to tenant {tenant_id}"
        )
        return point_ids

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

    def _build_payload_filter(
        self,
        tenant_id: int,
        *,
        must_match: dict[str, Any] | None = None,
        must_match_any: dict[str, list] | None = None,
        nested_match_any: dict[str, list] | None = None,
    ) -> models.Filter:
        """
        Build a Qdrant `Filter` from the engine-style filter fragments.

        - `must_match`: `{field: value}` → FieldCondition(MatchValue)
        - `must_match_any`: `{field: [values]}` → FieldCondition(MatchAny)
        - `nested_match_any`: `{array_field: [values]}` where the array
          holds objects — translates to a NestedCondition with a
          `MatchAny` on the nested `category`-like field. Concretely
          used for `topic_tags` (list of `{category, subtopic}` objects)
          where the key is the array field name and the values are the
          `category` strings to match.
        """
        must_conditions: list[models.FieldCondition] = [
            models.FieldCondition(
                key="tenant_id",
                match=models.MatchValue(value=tenant_id),
            )
        ]

        for field, value in (must_match or {}).items():
            if value is None:
                continue
            must_conditions.append(
                models.FieldCondition(
                    key=field,
                    match=models.MatchValue(value=value),
                )
            )

        for field, values in (must_match_any or {}).items():
            if not values:
                continue
            must_conditions.append(
                models.FieldCondition(
                    key=field,
                    match=models.MatchAny(any=list(values)),
                )
            )

        # Nested array-of-objects filter (e.g. topic_tags[].category).
        # Each key maps to a nested field; we match any object whose
        # `category` sub-field is in the provided values.
        nested_conditions: list[models.NestedCondition] = []
        for nested_field, values in (nested_match_any or {}).items():
            if not values:
                continue
            nested_conditions.append(
                models.NestedCondition(
                    nested=models.Nested(
                        key=nested_field,
                        filter=models.Filter(
                            must=[
                                models.FieldCondition(
                                    key="category",
                                    match=models.MatchAny(any=list(values)),
                                )
                            ]
                        ),
                    )
                )
            )

        return models.Filter(
            must=must_conditions,
            should=nested_conditions or None,
        )

    async def filter_chunks(
        self,
        tenant_id: int,
        *,
        must_match: dict[str, Any] | None = None,
        must_match_any: dict[str, list] | None = None,
        nested_match_any: dict[str, list] | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """
        Scroll chunks by payload filter (no vector query).

        Used by the heatmap citations path: retrieve all chunks for a
        given (source_id, topic) without computing an embedding.
        """
        try:
            collection_name = self._get_collection_name(tenant_id)

            scroll_filter = self._build_payload_filter(
                tenant_id,
                must_match=must_match,
                must_match_any=must_match_any,
                nested_match_any=nested_match_any,
            )

            try:
                await asyncio.to_thread(self.client.get_collection, collection_name)
            except Exception:
                logger.debug(
                    f"Qdrant collection {collection_name} does not exist; "
                    f"returning empty filter_chunks result"
                )
                return []

            collected: list[dict[str, Any]] = []
            offset = None
            # Qdrant scroll caps at 10000 per call; loop until we hit limit.
            page_size = min(limit, 10000)
            while len(collected) < limit:
                batch, next_offset = await asyncio.to_thread(
                    self.client.scroll,
                    collection_name=collection_name,
                    scroll_filter=scroll_filter,
                    limit=page_size,
                    offset=offset,
                    with_payload=True,
                    with_vectors=False,
                )
                if not batch:
                    break
                for point in batch:
                    payload = point.payload or {}
                    collected.append(
                        {
                            "id": str(point.id),
                            "text": payload.get("text", ""),
                            "metadata": {
                                k: v for k, v in payload.items() if k != "text"
                            },
                            "score": 1.0,
                        }
                    )
                    if len(collected) >= limit:
                        break
                if next_offset is None:
                    break
                offset = next_offset

            logger.info(
                f"filter_chunks: {len(collected)} chunks for tenant {tenant_id} "
                f"(must_match={must_match}, must_match_any={must_match_any}, "
                f"nested_match_any={nested_match_any})"
            )
            return collected[:limit]

        except Exception as e:
            logger.error(f"Error filtering chunks in Qdrant: {e}", exc_info=True)
            return []

    async def count_chunks(
        self,
        tenant_id: int,
        *,
        must_match: dict[str, Any] | None = None,
        must_match_any: dict[str, list] | None = None,
        nested_match_any: dict[str, list] | None = None,
    ) -> int:
        """
        Count chunks matching a payload filter using Qdrant's native count.

        Far cheaper than `filter_chunks` + `len()` for the Heatmap Engine,
        which counts chunk instances per district without materializing
        the chunks themselves.
        """
        try:
            collection_name = self._get_collection_name(tenant_id)

            count_filter = self._build_payload_filter(
                tenant_id,
                must_match=must_match,
                must_match_any=must_match_any,
                nested_match_any=nested_match_any,
            )

            try:
                await asyncio.to_thread(self.client.get_collection, collection_name)
            except Exception:
                logger.debug(
                    f"Qdrant collection {collection_name} does not exist; "
                    f"returning 0 count"
                )
                return 0

            result = await asyncio.to_thread(
                self.client.count,
                collection_name=collection_name,
                count_filter=count_filter,
                exact=True,
            )
            count = int(result.count)
            logger.info(
                f"count_chunks: {count} chunks for tenant {tenant_id} "
                f"(must_match={must_match}, must_match_any={must_match_any}, "
                f"nested_match_any={nested_match_any})"
            )
            return count

        except Exception as e:
            logger.error(f"Error counting chunks in Qdrant: {e}", exc_info=True)
            return 0

    async def update_metadata(
        self, chunk_ids: list[str], metadata: dict[str, Any], tenant_id: int
    ) -> bool:
        """Update metadata for chunks.

        Raises on failure rather than swallowing it -- callers (e.g. the
        batch-classification apply step) rely on the exception to mark a
        chunk 'failed' instead of treating a no-op write as success.
        """
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
