"""
Message schemas for request/response validation.
"""

from datetime import datetime
from typing import Any

from pydantic import BaseModel

from app.schemas.citations import CitationResponse
from app.schemas.rag import ImageResult


class MessageBase(BaseModel):
    """Base message schema"""

    content: str
    role: str  # user, assistant, system


class MessageCreate(BaseModel):
    """Schema for message creation"""

    content: str


class MessageResponse(MessageBase):
    """Schema for message response"""

    id: int
    conversation_id: int
    created_at: datetime
    citations: list[CitationResponse] = []
    images: list[ImageResult] | None = None

    # Token tracking fields
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    model_used: str | None = None

    class Config:
        from_attributes = True


class MessageListResponse(BaseModel):
    """Schema for paginated message list"""

    items: list[MessageResponse]
    total: int
    page: int
    per_page: int
    pages: int


class SendMessageRequest(BaseModel):
    """Schema for sending a message"""

    content: str
    chatbot_id: int


class SendMessageResponse(BaseModel):
    """Schema for send message response"""

    user_message: MessageResponse
    bot_message: MessageResponse
