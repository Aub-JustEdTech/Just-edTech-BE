"""
CRUD operations for upload batches.
"""

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.documents import Document, ProcessingStatus
from app.models.upload_batches import BatchStatus, UploadBatch


async def create_batch(
    db: AsyncSession, tenant_id: int, description: str | None = None
) -> UploadBatch:
    """Create a new upload batch."""
    batch = UploadBatch(
        batch_id=str(uuid.uuid4()),
        tenant_id=tenant_id,
        description=description,
        status=BatchStatus.PENDING,
    )
    db.add(batch)
    await db.commit()
    await db.refresh(batch)
    return batch


async def get_batch(
    db: AsyncSession, batch_id: str, tenant_id: int
) -> UploadBatch | None:
    """Get a batch by batch_id for a specific tenant."""
    result = await db.execute(
        select(UploadBatch).where(
            UploadBatch.batch_id == batch_id,
            UploadBatch.tenant_id == tenant_id,
        )
    )
    return result.scalar_one_or_none()


async def update_batch_counts(db: AsyncSession, batch_id: int):
    """
    Update batch counts based on current document statuses.

    This should be called after any document status change.
    """
    batch = await db.get(UploadBatch, batch_id)
    if not batch:
        return None

    # Get all documents in this batch
    result = await db.execute(
        select(Document).where(Document.upload_batch_id == batch_id)
    )
    documents = result.scalars().all()

    # Count by status
    batch.total_documents = len(documents)
    batch.pending_documents = sum(
        1 for d in documents if d.processing_status == ProcessingStatus.PENDING
    )
    batch.processing_documents = sum(
        1 for d in documents if d.processing_status == ProcessingStatus.PROCESSING
    )
    batch.completed_documents = sum(
        1 for d in documents if d.processing_status == ProcessingStatus.COMPLETED
    )
    batch.failed_documents = sum(
        1 for d in documents if d.processing_status == ProcessingStatus.FAILED
    )

    # Update batch status
    batch.update_status()

    await db.commit()
    await db.refresh(batch)
    return batch


async def list_batches(
    db: AsyncSession, tenant_id: int, skip: int = 0, limit: int = 100
) -> list[UploadBatch]:
    """List all batches for a tenant."""
    result = await db.execute(
        select(UploadBatch)
        .where(UploadBatch.tenant_id == tenant_id)
        .order_by(UploadBatch.created_at.desc())
        .offset(skip)
        .limit(limit)
    )
    return list(result.scalars().all())


async def get_batch_documents(
    db: AsyncSession, batch_id: int, tenant_id: int
) -> list[Document]:
    """Get all documents in a batch."""
    result = await db.execute(
        select(Document)
        .where(
            Document.upload_batch_id == batch_id,
            Document.tenant_id == tenant_id,
        )
        .order_by(Document.created_at.asc())
    )
    return list(result.scalars().all())
