"""
Utility functions for creating standardized API responses.
"""

from typing import Any

from fastapi import status
from fastapi.responses import JSONResponse
from fastapi.encoders import jsonable_encoder

from app.schemas.common import APIResponse, ErrorDetail


def success_response(
    data: Any = None,
    extra: dict[str, Any] | None = None,
    status_code: int = status.HTTP_200_OK,
) -> JSONResponse:
    """
    Create a standardized success response.

    Args:
        data: The response data
        extra: Additional metadata (pagination, timestamps, etc.)
        status_code: HTTP status code (default: 200)

    Returns:
        JSONResponse with standardized structure
    """
    response = APIResponse(
        success=True,
        data=data,
        error=None,
        extra=extra,
    )
    return JSONResponse(
        content=jsonable_encoder(response, exclude_none=False),
        status_code=status_code,
    )


def error_response(
    code: str,
    message: str,
    details: dict[str, Any] | None = None,
    status_code: int = status.HTTP_400_BAD_REQUEST,
    extra: dict[str, Any] | None = None,
) -> JSONResponse:
    """
    Create a standardized error response.

    Args:
        code: Error type (e.g., 'VALIDATION_ERROR', 'NOT_FOUND')
        message: Human-readable error message
        details: Additional error details
        status_code: HTTP status code (default: 400)
        extra: Additional metadata

    Returns:
        JSONResponse with standardized structure
    """
    error_detail = ErrorDetail(error_type=code, message=message, details=details)
    response = APIResponse(
        success=False,
        data=None,
        error=error_detail,
        extra=extra,
    )
    return JSONResponse(
        content=jsonable_encoder(response, exclude_none=False),
        status_code=status_code,
    )


def get_error_code_from_status(status_code: int) -> str:
    """
    Map HTTP status codes to error codes.

    Args:
        status_code: HTTP status code

    Returns:
        Error code string
    """
    error_code_map = {
        status.HTTP_400_BAD_REQUEST: "BAD_REQUEST",
        status.HTTP_401_UNAUTHORIZED: "UNAUTHORIZED",
        status.HTTP_403_FORBIDDEN: "FORBIDDEN",
        status.HTTP_404_NOT_FOUND: "NOT_FOUND",
        status.HTTP_409_CONFLICT: "CONFLICT",
        status.HTTP_413_REQUEST_ENTITY_TOO_LARGE: "FILE_TOO_LARGE",
        status.HTTP_422_UNPROCESSABLE_ENTITY: "VALIDATION_ERROR",
        status.HTTP_429_TOO_MANY_REQUESTS: "RATE_LIMIT_EXCEEDED",
        status.HTTP_500_INTERNAL_SERVER_ERROR: "INTERNAL_SERVER_ERROR",
        status.HTTP_502_BAD_GATEWAY: "BAD_GATEWAY",
        status.HTTP_503_SERVICE_UNAVAILABLE: "SERVICE_UNAVAILABLE",
    }
    return error_code_map.get(status_code, "UNKNOWN_ERROR")

