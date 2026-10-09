---
description: Dependency injection patterns for FastAPI
globs: app/utils/dependencies.py
alwaysApply: false
---

# Dependency Injection

Use FastAPI's dependency injection system for loose coupling and testability.

## Core Dependencies

```python
# app/utils/dependencies.py
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session
from app.db.connector import get_db

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login")

# Database dependency
def get_db_session() -> Session:
    db = get_db()
    try:
        yield db
    finally:
        db.close()

# Authentication dependency
def get_current_user(
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db_session)
):
    """Get authenticated user from token"""
    payload = decode_token(token)
    if not payload:
        raise HTTPException(status_code=401, detail="Invalid token")
    
    user = get_user_by_email(db, payload["sub"])
    if not user:
        raise HTTPException(status_code=401, detail="User not found")
    return user

# Service dependencies
def get_user_service(db: Session = Depends(get_db_session)) -> UserService:
    """Inject UserService with database session"""
    return UserService(db)
```

## Usage in Endpoints

```python
# app/api/endpoints/users.py
from fastapi import APIRouter, Depends
from app.utils.dependencies import get_current_user, get_user_service

router = APIRouter()

@router.get("/me")
def get_me(current_user = Depends(get_current_user)):
    """Current user endpoint - authentication injected"""
    return current_user

@router.get("/users/{user_id}")
def get_user(
    user_id: int,
    user_service: UserService = Depends(get_user_service)
):
    """User service injected"""
    return user_service.get_user(user_id)
```

## Dependency Chains

Dependencies can depend on other dependencies:

```python
def get_current_active_user(
    current_user = Depends(get_current_user)
):
    """Ensure user is active - depends on get_current_user"""
    if not current_user.is_active:
        raise HTTPException(status_code=400, detail="Inactive user")
    return current_user

def require_admin(
    current_user = Depends(get_current_active_user)
):
    """Require admin role - depends on get_current_active_user"""
    if not current_user.is_superuser:
        raise HTTPException(status_code=403, detail="Admin required")
    return current_user
```

## Benefits

- Easy to test with mocks
- Reusable across endpoints
- Clear dependency graph
- Type-safe injection
