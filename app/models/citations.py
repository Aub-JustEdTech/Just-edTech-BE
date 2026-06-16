"""
Citation model for message citations.
"""

from sqlalchemy import BigInteger, Column, ForeignKey, Integer, String, Text
from sqlalchemy.orm import relationship

from app.models.base import BaseModel


class Citation(BaseModel):
    """Citation model for document references in messages"""

    __tablename__ = "citations"

    message_id = Column(
        BigInteger, ForeignKey("messages.id", ondelete="CASCADE"), nullable=False
    )
    document_title = Column(String(500), nullable=True)
    document_url = Column(Text, nullable=False)
    page_number = Column(Integer, nullable=True)
    snippet = Column(Text, nullable=True)
    position = Column(Integer, nullable=True)

    # Relationships
    message = relationship("Message", back_populates="citations")
