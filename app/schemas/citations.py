"""
Citation schemas for request/response validation.
"""

from datetime import datetime

from pydantic import BaseModel


class CitationBase(BaseModel):
    """Base citation schema"""

    document_title: str | None = None
    document_url: str
    expires_in: int | None = None
    page_number: int | None = None
    snippet: str | None = None
    position: int | None = None


class CitationCreate(CitationBase):
    """Schema for citation creation"""

    ...


class CitationResponse(CitationBase):
    """Schema for citation response"""

    id: int
    message_id: int
    created_at: datetime

    class Config:
        from_attributes = True
