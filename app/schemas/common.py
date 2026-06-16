"""
Common response schemas for standardized API responses.
"""

from typing import Any, Generic, TypeVar

from pydantic import BaseModel, Field

T = TypeVar("T")


class ErrorDetail(BaseModel):
    """Error detail schema"""

    error_type: str = Field(..., description="Error type (e.g., 'VALIDATION_ERROR', 'NOT_FOUND')")
    message: str = Field(..., description="Human-readable error message")
    details: dict[str, Any] | None = Field(
        None, description="Additional error details or validation errors"
    )


class APIResponse(BaseModel, Generic[T]):
    """
    Standardized API response structure.

    This structure is used for all API responses to ensure consistency.
    """

    success: bool = Field(..., description="Whether the request was successful")
    data: T | None = Field(None, description="Response data (null on error)")
    error: ErrorDetail | None = Field(None, description="Error information (null on success)")
    extra: dict[str, Any] | None = Field(
        None, description="Additional metadata (pagination, timestamps, etc.)"
    )

    class Config:
        json_schema_extra = {
            "example": {
                "success": True,
                "data": {"id": 1, "name": "Example"},
                "error": None,
                "extra": {"timestamp": "2025-01-15T10:30:00Z"},
            }
        }

