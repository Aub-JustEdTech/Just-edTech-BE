"""
CRUD operations for Document model.
"""


from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.documents import Document


class DocumentCRUD:
    """CRUD operations for Document model"""

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


document = DocumentCRUD()
