"""
Image caption model for multimodal RAG.
Stores image metadata, captions, and file paths for retrieval.
"""

from sqlalchemy import BigInteger, Column, ForeignKey, Integer, String, Text
from sqlalchemy.orm import relationship

from app.models.base import BaseModel


class ImageCaption(BaseModel):
    """Image caption and metadata for multimodal RAG"""

    __tablename__ = "image_captions"

    # Document relationship
    document_id = Column(
        BigInteger,
        ForeignKey("documents.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    tenant_id = Column(
        BigInteger,
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Image file information
    image_file_path = Column(String, nullable=False)
    image_url = Column(String, nullable=True)  # S3 URL or presigned URL
    page_number = Column(Integer, nullable=False)
    image_index = Column(Integer, nullable=False)  # Index within the document

    # Caption information
    caption = Column(Text, nullable=False)  # Context-aware caption
    surrounding_text_before = Column(Text, nullable=True)  # Text before image
    surrounding_text_after = Column(Text, nullable=True)  # Text after image

    # Image metadata
    width = Column(Integer, nullable=True)
    height = Column(Integer, nullable=True)
    size_bytes = Column(Integer, nullable=True)

    # Relationships
    document = relationship("Document", backref="image_captions")
    tenant = relationship("Tenant", backref="image_captions")
