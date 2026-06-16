"""
Monitoring model per ERD.
"""

from sqlalchemy import BigInteger, Column, ForeignKey, Text
from sqlalchemy.orm import relationship

from app.models.base import BaseModel


class Monitoring(BaseModel):
    __tablename__ = "monitoring"

    id = Column(BigInteger, primary_key=True, index=True)
    tenant_id = Column(
        BigInteger, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    chatbot_config_id = Column(
        BigInteger, ForeignKey("chatbot_configs.id", ondelete="SET NULL"), nullable=True
    )
    logs = Column(Text, nullable=True)
    errors = Column(Text, nullable=True)
    tokens_count = Column(Text, nullable=True)
    request_count = Column(Text, nullable=True)

    tenant = relationship("Tenant", back_populates="monitoring")
    chatbot_config = relationship("ChatbotConfig")
