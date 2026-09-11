---
description: API endpoint patterns and conventions
globs: app/api/endpoints/**/*.py
alwaysApply: true
---

# API Endpoint Patterns

## Router Setup

```python
# app/api/endpoints/users.py
from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session
from app.db.connector import get_db
from app.schemas.users import UserCreate, UserResponse
from app.services.user_service import UserService
from app.utils.dependencies import get_current_user, get_user_service

router = APIRouter(
    prefix="/users",
    tags=["users"]
)
```

## Endpoint Patterns

### Create Resource
```python
@router.post("", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
def create_user(
    user: UserCreate,
    db: Session = Depends(get_db),
    service: UserService = Depends(get_user_service)
):
    """Create a new user"""
    return service.create_user(db, user)
```

### Get Single Resource
```python
@router.get("/{user_id}", response_model=UserResponse)
def get_user(
    user_id: int,
    db: Session = Depends(get_db),
    service: UserService = Depends(get_user_service)
):
    """Get user by ID"""
    return service.get_user(db, user_id)
```

### List Resources with Pagination
```python
@router.get("", response_model=PaginatedResponse[UserResponse])
def list_users(
    page: int = 1,
    page_size: int = 20,
    db: Session = Depends(get_db),
    service: UserService = Depends(get_user_service)
):
    """List users with pagination"""
    return service.list_users(db, page=page, page_size=page_size)
```

### Update Resource
```python
@router.put("/{user_id}", response_model=UserResponse)
def update_user(
    user_id: int,
    user_update: UserUpdate,
    db: Session = Depends(get_db),
    service: UserService = Depends(get_user_service),
    current_user = Depends(get_current_user)
):
    """Update user"""
    return service.update_user(db, user_id, user_update)
```

### Delete Resource
```python
@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_user(
    user_id: int,
    db: Session = Depends(get_db),
    service: UserService = Depends(get_user_service),
    current_user = Depends(require_admin)
):
    """Delete user (admin only)"""
    service.delete_user(db, user_id)
```

## Rules

1. **Response Models**: Always specify `response_model`
2. **Status Codes**: Use appropriate HTTP status codes
3. **Dependencies**: Inject all dependencies via `Depends()`
4. **Docstrings**: Add brief description for OpenAPI docs
5. **No Business Logic**: Delegate to service layer
6. **Authentication**: Use dependency injection for auth checks
7. **Validation**: Let Pydantic handle request validation

## Common Query Parameters

```python
from typing import Annotated
from fastapi import Query

# Pagination
def list_items(
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
):
    pass

# Filtering
def search_items(
    q: Annotated[str | None, Query(max_length=100)] = None,
    status: Annotated[str | None, Query()] = None,
):
    pass
```

## Error Responses

Let exception handlers format errors globally:

```python
# ✅ GOOD - Let service raise exceptions
@router.get("/{user_id}")
def get_user(user_id: int, service = Depends(get_user_service)):
    return service.get_user(user_id)  # Service raises NotFoundError

# ❌ BAD - Manual error handling
@router.get("/{user_id}")
def get_user(user_id: int, service = Depends(get_user_service)):
    try:
        return service.get_user(user_id)
    except NotFoundError:
        raise HTTPException(status_code=404, detail="Not found")
```
