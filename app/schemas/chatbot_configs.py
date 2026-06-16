"""
Chatbot configuration schemas for request/response validation.
"""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict


class ChatbotConfigBase(BaseModel):
    """Base chatbot config schema"""

    name: str
    title: str | None = None
    welcome_message: str | None = None
    bot_avatar: str | None = None
    is_default: bool = False


class ChatbotConfigCreate(ChatbotConfigBase):
    """Schema for chatbot configuration creation"""

    model_config = ConfigDict(extra='allow')
    
    tenant_id: int
    # Additional config fields can be added here and will be stored in config_version_history
    # Examples: embedding_model_id, chunk_size, etc.


class ChatbotConfigUpdate(BaseModel):
    """Schema for chatbot configuration updates"""

    model_config = ConfigDict(extra='allow')
    
    name: str | None = None
    title: str | None = None
    welcome_message: str | None = None
    bot_avatar: str | None = None
    is_default: bool | None = None
    # Additional config fields can be added here and will be stored in config_version_history


class ChatbotConfigResponse(ChatbotConfigBase):
    """Schema for chatbot configuration response"""

    id: int
    tenant_id: int
    tenant_name: str | None = None
    tenant_logo: str | None = None
    config_version_history: list[dict[str, Any]] | None = None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class ChatbotConfigListResponse(BaseModel):
    """Schema for paginated chatbot configuration list"""

    items: list[ChatbotConfigResponse]
    total: int
    page: int
    per_page: int
    pages: int


class ChatbotConfigDefaultsResponse(BaseModel):
    """Schema for chatbot configuration default values"""

    base_defaults: dict[str, Any]
    config_defaults: dict[str, Any]
