"""Celery tasks for document processing."""

import logging
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.celery_app import celery_app
from app.db.connector import AsyncSessionLocal
from app.models.documents import Document, ProcessingStatus
from app.models.processing_jobs import DocumentProcessingJob, JobStatus
from app.services.document_service import DocumentService
from app.tasks.loop_utils import get_event_loop

logger = logging.getLogger(__name__)


@celery_app.task(name="process_document", bind=True, max_retries=3)
def process_document_task(
    self, document_id: int, job_id: int, batch_id: int | None = None
):
    """
    Celery task to process a document in the background.

    Args:
        document_id: ID of the document to process
        job_id: ID of the processing job to track
        batch_id: Optional batch ID for updating batch status
    """
    try:
        logger.info(f"Starting processing for document {document_id}, job {job_id}")

        # Get or create event loop for this worker
        loop = get_event_loop()

        # Run async function in sync context
        loop.run_until_complete(_process_document_async(document_id, job_id, batch_id))

        logger.info(f"Successfully processed document {document_id}")

    except Exception as exc:
        logger.error(f"Failed to process document {document_id}: {exc}", exc_info=True)

        # Update job status to failed
        try:
            loop = get_event_loop()
            loop.run_until_complete(_mark_job_failed(job_id, str(exc)))
        except Exception as update_error:
            logger.error(f"Failed to update job status: {update_error}")

        # Retry with exponential backoff
        raise self.retry(exc=exc, countdown=60 * (2**self.request.retries)) from exc


async def _process_document_async(
    document_id: int, job_id: int, batch_id: int | None = None
):
    """Process document asynchronously."""
    async with AsyncSessionLocal() as db:
        try:
            # Update job status to processing
            await _update_job_status(db, job_id, JobStatus.PROCESSING)

            # Get document
            result = await db.execute(
                select(Document).where(Document.id == document_id)
            )
            document = result.scalar_one_or_none()

            if not document:
                raise ValueError(f"Document {document_id} not found")

            # Update document status
            document.processing_status = ProcessingStatus.PROCESSING
            await db.commit()

            # Update batch status if part of a batch
            if batch_id:
                await _update_batch_status(db, batch_id)

            # Initialize document service
            document_service = DocumentService()

            # Process the document
            start_time = datetime.utcnow()
            await document_service.process_document_background(
                db=db,
                document_id=document_id,
                job_id=job_id,
            )
            processing_time = (datetime.utcnow() - start_time).total_seconds()

            # Update job status to completed
            job = await db.get(DocumentProcessingJob, job_id)
            if job:
                job.status = JobStatus.COMPLETED
                job.processing_time_seconds = processing_time
                await db.commit()

            # Update batch status after completion
            if batch_id:
                await _update_batch_status(db, batch_id)

            logger.info(
                f"Document {document_id} processed successfully in {processing_time:.2f}s"
            )

        except Exception as e:
            logger.error(f"Error processing document {document_id}: {e}")
            await db.rollback()

            # Update batch status even on failure
            if batch_id:
                try:
                    await _update_batch_status(db, batch_id)
                except Exception as batch_error:
                    logger.error(f"Failed to update batch status: {batch_error}")

            raise


async def _update_job_status(
    db: AsyncSession, job_id: int, status: JobStatus, error_message: str | None = None
):
    """Update job status in database."""
    job = await db.get(DocumentProcessingJob, job_id)
    if job:
        job.status = status
        if error_message:
            job.error_message = error_message
        await db.commit()


async def _mark_job_failed(job_id: int, error_message: str):
    """Mark a job as failed with error message."""
    async with AsyncSessionLocal() as db:
        await _update_job_status(db, job_id, JobStatus.FAILED, error_message)


async def _update_batch_status(db: AsyncSession, batch_id: int):
    """Update batch status based on document statuses."""
    from app.crud import upload_batches as crud_batches

    try:
        await crud_batches.update_batch_counts(db=db, batch_id=batch_id)
        logger.info(f"Updated batch {batch_id} status")
    except Exception as e:
        logger.error(f"Failed to update batch {batch_id} status: {e}")
