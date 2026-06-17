"""
CRUD operations for Citation model.
"""


from sqlalchemy.ext.asyncio import AsyncSession

from app.models.citations import Citation
from app.schemas.citations import CitationCreate


class CitationCRUD:
    """CRUD operations for Citation model"""

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


citation = CitationCRUD()
