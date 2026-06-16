"""
Conversation schemas for request/response validation.
"""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel


class ConversationBase(BaseModel):
    """Base conversation schema"""

    title: str | None = None


class ConversationCreate(ConversationBase):
    """Schema for conversation creation - title is auto-generated"""

    pass


class ConversationUpdate(BaseModel):
    """Schema for conversation updates"""

    title: str | None = None


class ConversationResponse(ConversationBase):
    """Schema for conversation response"""

    id: int
    user_id: int | None = None
    chat_consumer_uuid: UUID | None = None
    tenant_id: int
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class ConversationListItem(BaseModel):
    """Schema for conversation list item with preview"""

    id: int
    title: str | None = None
    created_at: datetime
    updated_at: datetime
    last_message_preview: str | None = None

    class Config:
        from_attributes = True


class PaginationResponse(BaseModel):
    """Schema for paginated responses"""

    items: list
    total: int
    page: int
    per_page: int
    pages: int


class ConversationListResponse(PaginationResponse):
    """Schema for paginated conversation list"""

    items: list[ConversationListItem]
