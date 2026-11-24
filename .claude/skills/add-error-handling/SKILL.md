---
name: add-error-handling
description: Sets up custom exception infrastructure for a domain. Use when the user asks to add error handling, custom exceptions, set up exception classes, or wire exception handlers into the app.
allowed-tools: Read, Edit, Write, Bash
---

When adding error handling infrastructure, work through these steps in order.

## Step 1 — Custom Exception Classes

Create or update `app/utils/exceptions.py`:

```python
import logging
from typing import Any

logger = logging.getLogger(__name__)


class AppException(Exception):
    """Base exception for all application errors."""

    def __init__(self, message: str, status_code: int = 500) -> None:
        self.message = message
        self.status_code = status_code
        super().__init__(self.message)


class NotFoundError(AppException):
    def __init__(self, resource: str, identifier: Any) -> None:
        super().__init__(
            f"{resource} with id {identifier} not found",
            status_code=404,
        )


class ValidationError(AppException):
    def __init__(self, message: str) -> None:
        super().__init__(message, status_code=400)


class UnauthorizedError(AppException):
    def __init__(self, message: str = "Unauthorized") -> None:
        super().__init__(message, status_code=401)


class ConflictError(AppException):
    def __init__(self, message: str) -> None:
        super().__init__(message, status_code=409)
```

If `app/utils/exceptions.py` already exists, add only the missing classes — do not replace existing ones.

## Step 2 — Exception Handlers

Create or update `app/utils/exception_handlers.py`:

```python
import logging

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.utils.exceptions import AppException

logger = logging.getLogger(__name__)


def register_exception_handlers(app: FastAPI) -> None:

    @app.exception_handler(AppException)
    async def app_exception_handler(request: Request, exc: AppException) -> JSONResponse:
        logger.error("AppException on %s: %s", request.url.path, exc.message)
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "success": False,
                "data": None,
                "error": exc.message,
                "extra": None,
            },
        )

    @app.exception_handler(ValueError)
    async def value_error_handler(request: Request, exc: ValueError) -> JSONResponse:
        logger.error("ValueError on %s: %s", request.url.path, str(exc))
        return JSONResponse(
            status_code=400,
            content={
                "success": False,
                "data": None,
                "error": str(exc),
                "extra": None,
            },
        )
```

The error response envelope matches `success_response()` format so the frontend receives a consistent shape.

## Step 3 — Wire into main.py

Read `app/main.py` and add the registration call. It belongs inside the lifespan or immediately after `app = FastAPI(...)`:

```python
from app.utils.exception_handlers import register_exception_handlers

# After app = FastAPI(...):
register_exception_handlers(app)
```

Check if it is already wired before adding.

## Step 4 — Apply Layer Rules

Verify and update each layer for the domain:

### CRUD layer — return `None`, never raise
```python
# ✅ Correct
async def get(self, db: AsyncSession, resource_id: int) -> Resource | None:
    result = await db.execute(select(Resource).where(Resource.id == resource_id))
    return result.scalar_one_or_none()

# ❌ Wrong — CRUD should not raise domain exceptions
async def get(self, db: AsyncSession, resource_id: int) -> Resource:
    result = ...
    if not result:
        raise NotFoundError("Resource", resource_id)  # belongs in service
```

### Service layer — raise domain exceptions
```python
from app.utils.exceptions import NotFoundError, ValidationError, UnauthorizedError

async def get(self, db: AsyncSession, resource_id: int, tenant_id: int) -> ResourceResponse:
    obj = await resource_crud.get(db, resource_id)
    if not obj:
        raise NotFoundError("Resource", resource_id)
    if obj.tenant_id != tenant_id:
        raise UnauthorizedError()
    return ResourceResponse.model_validate(obj)
```

### Endpoint layer — do nothing, let handlers catch
```python
# ✅ Correct — exception propagates to handler automatically
@router.get("/{resource_id}")
async def get_resource(
    resource_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> JSONResponse:
    result = await resource_service.get(db, resource_id, current_user.tenant_id)
    return success_response(data=result)
```

## Constraints

- Never catch an exception just to re-raise it silently
- Always log before raising: `logger.error("...", exc_info=True)` or `logger.error("...", str(exc))`
- No raw `HTTPException` in service layer — raise domain exceptions only
- CRUD layer must not import from `app/utils/exceptions.py` — that belongs to the service layer
- The error envelope shape (`success`, `data`, `error`, `extra`) must match `success_response()` format
