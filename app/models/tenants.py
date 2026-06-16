"""
Tenant and TenantProject models.
"""

from sqlalchemy import BigInteger, Column, String
from sqlalchemy.orm import relationship

from app.models.base import BaseModel


class Tenant(BaseModel):
    __tablename__ = "tenants"

    id = Column(BigInteger, primary_key=True, index=True)
    name = Column(String, nullable=False, unique=True, index=True)
    domain = Column(String, nullable=False, unique=True, index=True)
    logo_url = Column(String, nullable=True)

    users = relationship("User", back_populates="tenant", cascade="all, delete-orphan")
    api_keys = relationship(
        "ApiKey", back_populates="tenant", cascade="all, delete-orphan"
    )
    conversations = relationship(
        "Conversation", back_populates="tenant", cascade="all, delete-orphan"
    )
    monitoring = relationship(
        "Monitoring", back_populates="tenant", cascade="all, delete-orphan"
    )
    billing = relationship(
        "Billing", back_populates="tenant", cascade="all, delete-orphan"
    )
    chatbot_configs = relationship(
        "ChatbotConfig", back_populates="tenant", cascade="all, delete-orphan"
    )
    invitations = relationship(
        "Invitation", back_populates="tenant", cascade="all, delete-orphan"
    )
    # LLM models are now global
    documents = relationship(
        "Document", back_populates="tenant", cascade="all, delete-orphan"
    )
    chat_consumers = relationship(
        "ChatConsumer", back_populates="tenant", cascade="all, delete-orphan"
    )
    daily_token_usage = relationship(
        "DailyTokenUsage", back_populates="tenant", cascade="all, delete-orphan"
    )
    monthly_billing = relationship(
        "MonthlyBilling", back_populates="tenant", cascade="all, delete-orphan"
    )
