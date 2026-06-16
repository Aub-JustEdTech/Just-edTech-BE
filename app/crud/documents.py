"""
CRUD operations for Document model.
"""


from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.documents import Document
from app.schemas.documents import DocumentCreate, DocumentUpdate


class DocumentCRUD:
    """CRUD operations for Document model"""

    async def get(self, db: AsyncSession, document_id: int) -> Document | None:
        """Get document by ID"""
        return await db.get(Document, document_id)

    async def get_by_owner(
        self, db: AsyncSession, owner_id: int, skip: int = 0, limit: int = 100
    ) -> list[Document]:
        """Get documents by owner"""
        result = await db.execute(
            select(Document)
            .where(Document.owner_id == owner_id)
            .offset(skip)
            .limit(limit)
        )
        return result.scalars().all()

    async def get_by_tenant(
        self, db: AsyncSession, tenant_id: int, skip: int = 0, limit: int = 100
    ) -> list[Document]:
        """Get documents by tenant"""
        result = await db.execute(
            select(Document)
            .where(Document.tenant_id == tenant_id)
            .offset(skip)
            .limit(limit)
        )
        return result.scalars().all()

    async def create(
        self, db: AsyncSession, document_create: DocumentCreate, owner_id: int
    ) -> Document:
        """Create new document"""
        db_document = Document(
            title=document_create.title,
            content=document_create.content,
            file_type=document_create.file_type,
            doc_metadata=document_create.metadata,
            owner_id=owner_id,
        )
        db.add(db_document)
        await db.commit()
        await db.refresh(db_document)
        return db_document

    async def update(
        self, db: AsyncSession, document_id: int, document_update: DocumentUpdate
    ) -> Document | None:
        """Update document"""
        db_document = await self.get(db, document_id)
        if not db_document:
            return None

        update_data = document_update.dict(exclude_unset=True)
        for field, value in update_data.items():
            setattr(db_document, field, value)

        await db.commit()
        await db.refresh(db_document)
        return db_document

    async def delete(self, db: AsyncSession, document_id: int) -> bool:
        """Delete document"""
        db_document = await self.get(db, document_id)
        if not db_document:
            return False

        db.delete(db_document)
        await db.commit()
        return True

    async def update_processing_status(
        self, db: AsyncSession, document_id: int, status: str
    ) -> Document | None:
        """Update document processing status"""
        db_document = await self.get(db, document_id)
        if not db_document:
            return None

        db_document.processing_status = status
        await db.commit()
        await db.refresh(db_document)
        return db_document


document = DocumentCRUD()
