---
description: Type safety and validation patterns
globs: app/**/*.py
alwaysApply: false
---

# Type Safety & Validation

## Type Hints

Always use type hints for better IDE support and error detection.

```python
# ✅ GOOD
def get_user(db: Session, user_id: int) -> User | None:
    return db.query(User).filter(User.id == user_id).first()

def process_items(items: list[str]) -> dict[str, int]:
    return {item: len(item) for item in items}

# ❌ BAD - No type hints
def get_user(db, user_id):
    return db.query(User).filter(User.id == user_id).first()
```

## Pydantic Schemas

Use Pydantic for validation and serialization:

```python
# app/schemas/users.py
from pydantic import BaseModel, EmailStr, Field, field_validator, ConfigDict
from datetime import datetime

class UserBase(BaseModel):
    email: EmailStr
    full_name: str = Field(..., min_length=1, max_length=100)

class UserCreate(UserBase):
    password: str = Field(..., min_length=8)
    
    @field_validator('password')
    @classmethod
    def validate_password(cls, v: str) -> str:
        if not any(c.isupper() for c in v):
            raise ValueError('Password must contain uppercase letter')
        if not any(c.isdigit() for c in v):
            raise ValueError('Password must contain digit')
        return v

class UserResponse(UserBase):
    id: int
    is_active: bool
    created_at: datetime
    
    model_config = ConfigDict(from_attributes=True)
```

## Optional Types

Use `| None` for optional values (Python 3.10+):

```python
# ✅ GOOD
def get_user(user_id: int) -> User | None:
    pass

# ✅ Also acceptable
from typing import Optional
def get_user(user_id: int) -> Optional[User]:
    pass
```

## Generic Types

Use generics for reusable components:

```python
from typing import TypeVar, Generic

T = TypeVar('T')

class PaginatedResponse(BaseModel, Generic[T]):
    items: list[T]
    total: int
    page: int
    page_size: int
```

## Rules

1. **Always** use type hints for function parameters and returns
2. **Always** use Pydantic models for API request/response
3. **Use** `| None` for optional values
4. **Validate** complex data with Pydantic validators
5. **Never** use `Any` unless absolutely necessary
