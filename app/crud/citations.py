"""
CRUD operations for Citation model.
"""


from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.citations import Citation
from app.schemas.citations import CitationCreate


class CitationCRUD:
    """CRUD operations for Citation model"""

    async def create_citation(
        self, db: AsyncSession, message_id: int, citation_data: CitationCreate
    ) -> Citation:
        """Create single citation"""
        db_citation = Citation(
            message_id=message_id,
            document_title=citation_data.document_title,
            document_url=citation_data.document_url,
            page_number=getattr(citation_data, "page_number", None),
            snippet=citation_data.snippet,
            position=citation_data.position,
        )
        db.add(db_citation)
        await db.commit()
        await db.refresh(db_citation)
        return db_citation

    async def create_citations_bulk(
        self, db: AsyncSession, message_id: int, citations_data: list[CitationCreate]
    ) -> list[Citation]:
        """Create multiple citations for a message"""
        citations = []
        for citation_data in citations_data:
            db_citation = Citation(
                message_id=message_id,
                document_title=citation_data.document_title,
                document_url=citation_data.document_url,
                page_number=getattr(citation_data, "page_number", None),
                snippet=citation_data.snippet,
                position=citation_data.position,
            )
            citations.append(db_citation)

        db.add_all(citations)
        await db.flush()
        for citation in citations:
            await db.refresh(citation)
        await db.commit()

        return citations

    async def get_message_citations(
        self, db: AsyncSession, message_id: int
    ) -> list[Citation]:
        """Get all citations for a message"""
        citations_query = (
            select(Citation)
            .where(Citation.message_id == message_id)
            .order_by(Citation.position.asc().nullslast(), Citation.created_at.asc())
        )

        result = await db.execute(citations_query)
        return result.scalars().all()


citation = CitationCRUD()
