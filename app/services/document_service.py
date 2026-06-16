"""
Main document service orchestrator.
Coordinates document processing, chunking, embedding, and vector storage.
"""

import logging
import os
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.documents import Document, ProcessingStatus
from app.models.processing_jobs import DocumentProcessingJob, JobStatus
from app.schemas.documents import DocumentSortField, SortOrder
from app.services.chatbot_config_service import chatbot_config_service
from app.services.document_processing.chunker import Chunker
from app.services.document_processing.factory import ProcessorFactory
from app.services.embeddings.embedding_service import EmbeddingService
from app.services.vector_store.factory import VectorStoreFactory, VectorStoreType
from app.utils.s3 import S3Manager

logger = logging.getLogger(__name__)


class DocumentService:
    """Main service for document operations"""

    def __init__(self):
        self.s3_manager = S3Manager(
            bucket_name=settings.S3_BUCKET_NAME,
            region_name=settings.S3_REGION,
            aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
            aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
        )
        self.embedding_service = EmbeddingService()
        self.vector_store = VectorStoreFactory.create(
            VectorStoreType(settings.VECTOR_STORE_TYPE)
        )
        os.makedirs(settings.TEMP_UPLOAD_DIR, exist_ok=True)

    async def upload_document(
        self,
        db: AsyncSession,
        file_content: bytes,
        file_name: str,
        tenant_id: int,
        file_size: int,
        upload_batch_id: int | None = None,
    ) -> Document:
        """
        Upload a document to S3 and create database record.

        Args:
            db: Database session
            file_content: File content as bytes
            file_name: Original file name
            tenant_id: Tenant ID
            file_size: File size in bytes
            upload_batch_id: Optional batch ID for bulk upload tracking

        Returns:
            Created Document object
        """
        # 1. Validate file type and size
        file_extension = os.path.splitext(file_name)[1].lower()
        if file_extension not in settings.ALLOWED_DOCUMENT_TYPES:
            raise ValueError(f"File type {file_extension} is not allowed.")
        if file_size > settings.MAX_FILE_SIZE_MB * 1024 * 1024:
            raise ValueError(f"File size exceeds {settings.MAX_FILE_SIZE_MB} MB limit.")

        # 2. Generate unique document ID and S3 path
        doc_uuid = str(uuid.uuid4())
        s3_key = f"tenants/{tenant_id}/documents/{doc_uuid}{file_extension}"

        # 3. Upload to S3
        await self.s3_manager.upload_file_object(file_content, s3_key)
        s3_url = f"s3://{settings.S3_BUCKET_NAME}/{s3_key}"

    async def upload_document_stream(
        self,
        db: AsyncSession,
        fileobj,
        file_name: str,
        tenant_id: int,
        file_size: int,
        upload_batch_id: int | None = None,
    ) -> Document:
        """
        Upload a document to S3 using streaming (memory-efficient) and create database record.

        Args:
            db: Database session
            fileobj: File-like object to stream (e.g., UploadFile.file)
            file_name: Original file name
            tenant_id: Tenant ID
            file_size: File size in bytes
            upload_batch_id: Optional batch ID for bulk upload tracking

        Returns:
            Created Document object
        """
        # 1. Validate file type and size
        file_extension = os.path.splitext(file_name)[1].lower()
        if file_extension not in settings.ALLOWED_DOCUMENT_TYPES:
            raise ValueError(f"File type {file_extension} is not allowed.")
        if file_size > settings.MAX_FILE_SIZE_MB * 1024 * 1024:
            raise ValueError(f"File size exceeds {settings.MAX_FILE_SIZE_MB} MB limit.")

        # 2. Generate unique document ID and S3 path
        doc_uuid = str(uuid.uuid4())
        s3_key = f"tenants/{tenant_id}/documents/{doc_uuid}{file_extension}"

        # 3. Upload to S3 via streaming
        await self.s3_manager.upload_fileobj_stream(fileobj, s3_key)
        s3_url = f"s3://{settings.S3_BUCKET_NAME}/{s3_key}"

        # 4. Create Document record in PostgreSQL
        document = Document(
            name=file_name,
            doc_id=doc_uuid,
            s3_url=s3_url,
            tenant_id=tenant_id,
            document_type=file_extension,
            processing_status=ProcessingStatus.PENDING,
            file_size_bytes=file_size,
            upload_batch_id=upload_batch_id,
        )
        db.add(document)
        await db.commit()
        await db.refresh(document)

        # 5. Create DocumentProcessingJob record
        job = DocumentProcessingJob(
            document_id=document.id,
            status=JobStatus.PENDING,
            processor_type=file_extension,
        )
        db.add(job)
        await db.commit()
        await db.refresh(job)

        logger.info(
            f"Uploaded document {document.id} ({file_name}) for tenant {tenant_id} via streaming"
        )

        return document

    async def process_document_background(
        self,
        db: AsyncSession,
        document_id: int,
        job_id: int,
    ):
        """
        Process a document in the background.

        Steps:
        1. Download from S3
        2. Extract text and metadata
        3. Chunk text
        4. Generate embeddings
        5. Store in vector database
        6. Update status
        """
        document = await db.get(Document, document_id)
        job = await db.get(DocumentProcessingJob, job_id)

        if not document or not job:
            logger.error(
                f"Document or Job not found: doc_id={document_id}, job_id={job_id}"
            )
            return

        job.status = JobStatus.PROCESSING
        document.processing_status = ProcessingStatus.PROCESSING
        await db.commit()

        temp_file_path = None
        try:
            # 1. Get default chatbot config for chunking and embedding settings
            chatbot_config_obj = await chatbot_config_service.get_default_chatbot_config(
                db, document.tenant_id
            )

            if not chatbot_config_obj:
                raise ValueError(
                    f"Default chatbot config not configured for tenant {document.tenant_id}"
                )

            # Get embedding model config from version history
            embedding_config = await chatbot_config_service.get_embedding_model_config(
                db, chatbot_config_obj.id
            )
            embedding_model_name = embedding_config.get("model")
            if not embedding_model_name:
                raise ValueError(
                    f"Embedding model not configured for chatbot {chatbot_config_obj.id}"
                )

            # Get chunking config from version history
            chunking_config = await chatbot_config_service.get_chunking_config(
                db, chatbot_config_obj.id
            )

            # 2. Download document from S3
            temp_file_path = os.path.join(
                settings.TEMP_UPLOAD_DIR, f"{document.doc_id}{document.document_type}"
            )
            s3_key = document.s3_url.split(f"{settings.S3_BUCKET_NAME}/")[1]
            await self.s3_manager.download_file_object(s3_key, temp_file_path)

            # 3. Extract text and metadata using appropriate processor
            processor = ProcessorFactory.get_processor(temp_file_path)
            extracted_text = processor.extract_text(temp_file_path)
            doc_metadata = processor.extract_metadata(temp_file_path)

            # 4. Chunk the text
            chunker = Chunker(
                chunk_size=chunking_config["chunk_size"],
                chunk_overlap=chunking_config["chunk_overlap"],
            )
            chunks = chunker.chunk_text(extracted_text)

            if not chunks:
                raise ValueError("No chunks generated from document")

            # 5. Generate embeddings for chunks
            embeddings = await self.embedding_service.generate_embeddings(
                chunks,
                model=embedding_model_name,
            )

            # 6. Prepare metadata for vector store
            metadatas = []
            for i, _chunk_text in enumerate(chunks):
                chunk_metadata = {
                    "document_id": document.doc_id,
                    "document_name": document.name,
                    "chunk_index": i,
                    "tenant_id": document.tenant_id,
                    "document_type": document.document_type,
                    "created_at": datetime.utcnow().isoformat(),
                    **doc_metadata,  # Merge document-level metadata
                }
                metadatas.append(chunk_metadata)

            # 7. Store chunks and embeddings in vector store
            await self.vector_store.add_chunks(
                document_id=document.doc_id,
                chunks=chunks,
                embeddings=embeddings,
                metadatas=metadatas,
            )

            # 8. Update job and document status
            job.status = JobStatus.COMPLETED
            job.chunks_created = len(chunks)
            job.processing_time_seconds = (
                datetime.utcnow() - job.updated_at
            ).total_seconds()

            document.processing_status = ProcessingStatus.COMPLETED
            document.chunk_count = len(chunks)

            logger.info(
                f"Successfully processed document {document_id} ({len(chunks)} chunks)"
            )

        except Exception as e:
            logger.error(f"Error processing document {document.id}: {e}", exc_info=True)
            job.status = JobStatus.FAILED
            job.error_message = str(e)
            document.processing_status = ProcessingStatus.FAILED
            document.error_message = str(e)
        finally:
            if temp_file_path and os.path.exists(temp_file_path):
                os.remove(temp_file_path)
            await db.commit()
            await db.refresh(job)
            await db.refresh(document)

    async def search_documents(
        self,
        db: AsyncSession,
        query: str,
        tenant_id: int,
        limit: int = 5,
        filters: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """
        Perform semantic search across documents.

        Args:
            db: Database session
            query: Search query
            tenant_id: Tenant ID
            limit: Maximum number of results
            filters: Additional metadata filters

        Returns:
            List of search results with chunks and metadata
        """
        # 1. Get default chatbot config for embedding model
        chatbot_config_obj = await chatbot_config_service.get_default_chatbot_config(
            db, tenant_id
        )

        if not chatbot_config_obj:
            raise ValueError(f"Default chatbot config not configured for tenant {tenant_id}")

        # Get embedding model config from version history
        embedding_config = await chatbot_config_service.get_embedding_model_config(
            db, chatbot_config_obj.id
        )
        embedding_model_name = embedding_config.get("model")
        if not embedding_model_name:
            raise ValueError(
                f"Embedding model not configured for chatbot {chatbot_config_obj.id}"
            )

        # 2. Generate embedding for the query
        query_embeddings = await self.embedding_service.generate_embeddings(
            [query],
            model=embedding_model_name,
        )

        if not query_embeddings:
            raise RuntimeError("Failed to generate embedding for query.")

        # 3. Search in the vector store
        results = await self.vector_store.search(
            query_embedding=query_embeddings[0],
            tenant_id=tenant_id,
            limit=limit,
            filters=filters,
        )

        logger.info(f"Search returned {len(results)} results for query: {query[:50]}")
        return results

    async def get_documents_by_type(
        self,
        db: AsyncSession,
        tenant_id: int,
        document_type: str | None = None,
        status: ProcessingStatus | None = None,
        exclude_failed: bool = False,
        skip: int = 0,
        limit: int = 100,
        search: str | None = None,
        sort_by: DocumentSortField = DocumentSortField.CREATED_AT,
        sort_order: SortOrder = SortOrder.DESC,
    ) -> list[Document]:
        """
        Get documents filtered by type and/or status.

        Args:
            db: Database session
            tenant_id: Tenant ID
            document_type: Filter by document type (.pdf, .md, etc.)
            status: Filter by processing status (if set, takes precedence over exclude_failed)
            exclude_failed: If True and status is None, exclude documents with processing_status=failed
            skip: Number of records to skip
            limit: Maximum number of records to return
            search: Case-insensitive substring to match against document name
            sort_by: Field to sort results by
            sort_order: Sort order (ascending/descending)

        Returns:
            List of Document objects
        """
        query = select(Document).where(Document.tenant_id == tenant_id)

        if document_type:
            query = query.where(Document.document_type == document_type)
        if status is not None:
            query = query.where(Document.processing_status == status)
        elif exclude_failed:
            query = query.where(Document.processing_status != ProcessingStatus.FAILED)

        if search:
            query = query.where(Document.name.ilike(f"%{search}%"))

        sort_field_map = {
            DocumentSortField.NAME: Document.name,
            DocumentSortField.CREATED_AT: Document.created_at,
            DocumentSortField.UPDATED_AT: Document.updated_at,
            DocumentSortField.FILE_SIZE_BYTES: Document.file_size_bytes,
            DocumentSortField.CHUNK_COUNT: Document.chunk_count,
            DocumentSortField.DOCUMENT_TYPE: Document.document_type,
            DocumentSortField.PROCESSING_STATUS: Document.processing_status,
        }
        sort_column = sort_field_map.get(sort_by, Document.created_at)
        if sort_order == SortOrder.ASC:
            order_clause = [sort_column.asc(), Document.id.asc()]
        else:
            order_clause = [sort_column.desc(), Document.id.desc()]

        query = query.order_by(*order_clause).offset(skip).limit(limit)

        result = await db.execute(query)
        documents = result.scalars().all()
        return documents

    async def delete_document(
        self, db: AsyncSession, document_id: int, tenant_id: int
    ) -> bool:
        """
        Delete a document and all associated data.

        Args:
            db: Database session
            document_id: Document ID
            tenant_id: Tenant ID (for verification)

        Returns:
            True if successful, False otherwise
        """
        document = await db.get(Document, document_id)
        if not document or document.tenant_id != tenant_id:
            return False

        await self.delete_document_instance(db, document)
        logger.info(f"Deleted document {document_id}")
        return True

    async def delete_document_instance(
        self,
        db: AsyncSession,
        document: Document,
    ) -> None:
        """
        Delete the provided document instance and associated data.

        Args:
            db: Database session
            document: Document ORM instance (tenant already validated)
        """
        try:
            # Delete from S3
            s3_key = document.s3_url.split(f"{settings.S3_BUCKET_NAME}/")[1]
            await self.s3_manager.delete_file_object(s3_key)

            # Delete from vector store
            embeddings_deleted = await self.vector_store.delete_document(
                document.doc_id, document.tenant_id
            )
            if not embeddings_deleted:
                raise RuntimeError(
                    f"Failed to delete embeddings for document {document.doc_id}"
                )

            # Delete from PostgreSQL (cascade will delete processing jobs)
            await db.delete(document)
            await db.commit()
        except Exception as e:
            await db.rollback()
            logger.error(
                f"Error deleting document {document.id}: {e}",
                exc_info=True,
            )
            raise
