"""
ChatConsumer model for lightweight chat users identified by UUID.
"""

import uuid

from sqlalchemy import BigInteger, Column, ForeignKey
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.models.base import BaseModel


class ChatConsumer(BaseModel):
    """Chat consumer model for lightweight chat users"""

    __tablename__ = "chat_consumers"

    chat_consumer_uuid = Column(
        UUID(as_uuid=True), unique=True, index=True, nullable=False, default=uuid.uuid4
    )
    tenant_id = Column(
        BigInteger, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )

    # Relationships
    tenant = relationship("Tenant", back_populates="chat_consumers")
    conversations = relationship("Conversation", back_populates="chat_consumer")
