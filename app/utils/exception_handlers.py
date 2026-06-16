"""
Global exception handlers for standardized error responses.
"""

import logging
import traceback

from fastapi import Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.schemas.common import APIResponse, ErrorDetail
from app.utils.response import get_error_code_from_status

logger = logging.getLogger(__name__)


async def http_exception_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    """
    Handle HTTPException and convert to standardized response format.

    Args:
        request: FastAPI request object
        exc: HTTPException instance

    Returns:
        JSONResponse with standardized error structure
    """
    # Extract error details from exception
    detail = exc.detail
    status_code = exc.status_code

    # Handle different detail formats
    if isinstance(detail, dict):
        message = detail.get("message", "An error occurred")
        error_details = {k: v for k, v in detail.items() if k != "message"}
    elif isinstance(detail, str):
        message = detail
        error_details = None
    else:
        message = str(detail)
        error_details = None

    error_code = get_error_code_from_status(status_code)

    error_detail = ErrorDetail(error_type=error_code, message=message, details=error_details)
    response = APIResponse(
        success=False,
        data=None,
        error=error_detail,
        extra=None,
    )

    return JSONResponse(
        content=response.model_dump(exclude_none=False),
        status_code=status_code,
    )


async def validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    """
    Handle validation errors and convert to standardized response format.

    Args:
        request: FastAPI request object
        exc: RequestValidationError instance

    Returns:
        JSONResponse with standardized error structure
    """
    errors = exc.errors()
    error_details = {}

    # Format validation errors
    for error in errors:
        field = ".".join(str(loc) for loc in error.get("loc", []))
        error_details[field] = {
            "type": error.get("type"),
            "message": error.get("msg"),
        }

    error_detail = ErrorDetail(
        error_type="VALIDATION_ERROR",
        message="Request validation failed",
        details=error_details,
    )
    response = APIResponse(
        success=False,
        data=None,
        error=error_detail,
        extra=None,
    )

    return JSONResponse(
        content=response.model_dump(exclude_none=False),
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
    )


async def general_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """
    Handle unexpected exceptions and convert to standardized response format.

    Args:
        request: FastAPI request object
        exc: Exception instance

    Returns:
        JSONResponse with standardized error structure
    """
    # Log the full exception with traceback
    logger.error(
        f"Unhandled exception in {request.method} {request.url.path}: {type(exc).__name__}: {exc}",
        exc_info=True
    )
    
    error_detail = ErrorDetail(
        error_type="INTERNAL_SERVER_ERROR",
        message="An unexpected error occurred",
        details={"exception_type": type(exc).__name__, "message": str(exc)} if exc else None,
    )
    response = APIResponse(
        success=False,
        data=None,
        error=error_detail,
        extra=None,
    )

    return JSONResponse(
        content=response.model_dump(exclude_none=False),
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
    )

