---
description: Error handling and exception patterns for FastAPI
globs: app/**/*.py
alwaysApply: false
---

# Error Handling Patterns

## Exception Hierarchy

Create custom exceptions for different error types:

```python
# app/utils/exceptions.py
class AppException(Exception):
    """Base exception for application"""
    def __init__(self, message: str, status_code: int = 500):
        self.message = message
        self.status_code = status_code
        super().__init__(self.message)

class NotFoundError(AppException):
    def __init__(self, resource: str, identifier: Any):
        super().__init__(
            f"{resource} with id {identifier} not found",
            status_code=404
        )

class ValidationError(AppException):
    def __init__(self, message: str):
        super().__init__(message, status_code=400)

class UnauthorizedError(AppException):
    def __init__(self, message: str = "Unauthorized"):
        super().__init__(message, status_code=401)
```

## Exception Handlers

```python
# app/utils/exception_handlers.py
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from app.utils.exceptions import AppException

def register_exception_handlers(app: FastAPI):
    
    @app.exception_handler(AppException)
    async def app_exception_handler(request: Request, exc: AppException):
        return JSONResponse(
            status_code=exc.status_code,
            content={"detail": exc.message}
        )
    
    @app.exception_handler(ValueError)
    async def value_error_handler(request: Request, exc: ValueError):
        return JSONResponse(
            status_code=400,
            content={"detail": str(exc)}
        )
```

## Usage in Layers

### CRUD Layer
```python
# ✅ Let exceptions propagate
def get_user(db: Session, user_id: int) -> User | None:
    return db.query(User).filter(User.id == user_id).first()
```

### Service Layer
```python
# ✅ Raise domain-specific exceptions
def get_user(self, db: Session, user_id: int) -> User:
    user = user_crud.get_user(db, user_id)
    if not user:
        raise NotFoundError("User", user_id)
    return user
```

### Endpoint Layer
```python
# ✅ Let exception handlers catch errors
@router.get("/users/{user_id}")
def get_user(user_id: int, service: UserService = Depends()):
    return service.get_user(user_id)  # Exceptions handled globally
```

## Rules

1. **CRUD**: Return None for not found, let DB errors propagate
2. **Services**: Raise custom exceptions for business logic errors
3. **Endpoints**: Let exception handlers catch and format errors
4. **Never**: Catch exceptions just to re-raise them
5. **Always**: Log errors before raising
