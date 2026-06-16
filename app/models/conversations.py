"""
Conversation and Message models for chat functionality.
"""

from sqlalchemy import BigInteger, Column, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship

from app.models.base import BaseModel


class Conversation(BaseModel):
    """Conversation model for chat sessions"""

    __tablename__ = "conversations"

    title = Column(String(255), nullable=True)

    # Foreign keys
    tenant_id = Column(
        BigInteger, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    user_id = Column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=True
    )
    chat_consumer_id = Column(
        BigInteger, ForeignKey("chat_consumers.id", ondelete="CASCADE"), nullable=True
    )
    chatbot_config_id = Column(
        BigInteger, ForeignKey("chatbot_configs.id", ondelete="SET NULL"), nullable=True
    )
    chatbot_config_version_index = Column(Integer, nullable=True)

    # Relationships
    tenant = relationship("Tenant", back_populates="conversations")
    user = relationship("User", back_populates="conversations")
    chat_consumer = relationship("ChatConsumer", back_populates="conversations")
    messages = relationship(
        "Message", back_populates="conversation", cascade="all, delete-orphan"
    )
    feedback = relationship(
        "Feedback", back_populates="conversation", cascade="all, delete-orphan"
    )
    documents = relationship(
        "Document",
        secondary="conversation_documents",
        back_populates="conversations",
    )
    chatbot_config = relationship("ChatbotConfig", back_populates="conversations")

    @property
    def chat_consumer_uuid(self):
        """Get the chat consumer UUID from the relationship"""
        return self.chat_consumer.chat_consumer_uuid if self.chat_consumer else None


class Message(BaseModel):
    """Message model for individual messages in conversations"""

    __tablename__ = "messages"

    conversation_id = Column(
        BigInteger, ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False
    )
    role = Column(String(50), nullable=False)  # user, assistant, system
    content = Column(Text, nullable=False)

    # Token tracking fields
    input_tokens = Column(BigInteger, nullable=True)
    output_tokens = Column(BigInteger, nullable=True)
    total_tokens = Column(BigInteger, nullable=True)
    model_used = Column(String(100), nullable=True)
    
    # Multimodal support - store images retrieved for this message
    images = Column(JSONB, nullable=True)

    # Relationships
    conversation = relationship("Conversation", back_populates="messages")
    citations = relationship(
        "Citation", back_populates="message", cascade="all, delete-orphan"
    )
