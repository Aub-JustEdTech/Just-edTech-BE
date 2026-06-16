"""
ChatConsumer schemas for request/response validation.
"""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel


class ChatConsumerCreate(BaseModel):
    """Schema for chat consumer creation"""

    tenant_id: int


class ChatConsumerResponse(BaseModel):
    """Schema for chat consumer response"""

    id: int
    chat_consumer_uuid: UUID
    tenant_id: int
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class ChatConsumerRegisterRequest(BaseModel):
    """Schema for chat consumer registration request"""

    tenant_id: int


class ChatConsumerRegisterResponse(BaseModel):
    """Schema for chat consumer registration response"""

    chat_consumer_uuid: UUID
    tenant_id: int
    message: str = "Chat consumer registered successfully"
