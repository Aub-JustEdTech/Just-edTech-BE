"""
LLM Model schemas for request/response validation.
"""

from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel


class LLMModelResponse(BaseModel):
    """Response for LLM model"""

    id: int
    name: str
    provider: str
    config: dict[str, Any] | None
    input_token_price: Decimal | None
    output_token_price: Decimal | None
    cache_token_price: Decimal | None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True

