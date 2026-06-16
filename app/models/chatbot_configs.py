"""
Chatbot configuration, performance metrics, and ML models per ERD.
"""

from sqlalchemy import BigInteger, Boolean, Column, Float, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship

from app.models.base import BaseModel


class ChatbotConfig(BaseModel):
    __tablename__ = "chatbot_configs"

    id = Column(BigInteger, primary_key=True, index=True)
    tenant_id = Column(
        BigInteger, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )

    # Chatbot identification fields
    name = Column(String, nullable=False)
    title = Column(String, nullable=True)
    welcome_message = Column(Text, nullable=True)
    bot_avatar = Column(String, nullable=True)
    is_default = Column(Boolean, default=False, nullable=False)

    # Version history for configuration changes (all config stored here)
    config_version_history = Column(JSONB, nullable=True)

    # Relationships
    tenant = relationship("Tenant", back_populates="chatbot_configs")
    performance_metrics = relationship(
        "PerformanceMetric",
        back_populates="chatbot_config",
        cascade="all, delete-orphan",
    )
    conversations = relationship(
        "Conversation", back_populates="chatbot_config"
    )


class PerformanceMetric(BaseModel):
    __tablename__ = "performance_metrics"

    id = Column(BigInteger, primary_key=True, index=True)
    retrieval_recall = Column(Float, nullable=True)
    llm_eval = Column(Float, nullable=True)
    bleu = Column(Float, nullable=True)
    accuracy = Column(Float, nullable=True)
    precision = Column(Float, nullable=True)
    f1_score = Column(Float, nullable=True)
    confidence_score = Column(Float, nullable=True)

    chatbot_config_id = Column(
        BigInteger, ForeignKey("chatbot_configs.id", ondelete="CASCADE"), nullable=False
    )

    chatbot_config = relationship("ChatbotConfig", back_populates="performance_metrics")

