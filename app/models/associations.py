"""
Association tables for many-to-many relations.
"""

from sqlalchemy import BigInteger, Column, ForeignKey, Table, UniqueConstraint

from app.models.base import Base

conversation_documents = Table(
    "conversation_documents",
    Base.metadata,
    Column(
        "conversation_id",
        BigInteger,
        ForeignKey("conversations.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column(
        "document_id",
        BigInteger,
        ForeignKey("documents.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    UniqueConstraint("conversation_id", "document_id", name="uq_conversation_document"),
)
