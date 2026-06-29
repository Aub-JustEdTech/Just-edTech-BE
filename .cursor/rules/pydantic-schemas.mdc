---
description: Pydantic schema patterns and validation
globs: app/schemas/**/*.py
alwaysApply: false
---

# Pydantic Schema Patterns

## Schema Organization

Organize schemas by domain entity with clear naming:

```python
# app/schemas/users.py
from pydantic import BaseModel, EmailStr, Field, ConfigDict
from datetime import datetime

# Base schema with common fields
class UserBase(BaseModel):
    email: EmailStr
    full_name: str | None = None

# Request schemas
class UserCreate(UserBase):
    password: str = Field(..., min_length=8)

class UserUpdate(BaseModel):
    full_name: str | None = None
    password: str | None = None

# Response schemas
class UserResponse(UserBase):
    id: int
    is_active: bool
    created_at: datetime
    
    model_config = ConfigDict(from_attributes=True)

class UserListResponse(BaseModel):
    users: list[UserResponse]
    total: int
```

## Common Patterns

### Pagination Schema
```python
# app/schemas/common.py
from typing import Generic, TypeVar

T = TypeVar('T')

class PaginatedResponse(BaseModel, Generic[T]):
    items: list[T]
    total: int
    page: int
    page_size: int
    
    @property
    def total_pages(self) -> int:
        return (self.total + self.page_size - 1) // self.page_size
```

### Success Response
```python
class SuccessResponse(BaseModel):
    message: str
    data: dict | None = None
```

### Error Response
```python
class ErrorResponse(BaseModel):
    detail: str
    error_code: str | None = None
```

## Validation

```python
from pydantic import field_validator, model_validator

class UserCreate(BaseModel):
    email: EmailStr
    password: str
    confirm_password: str
    
    @field_validator('password')
    @classmethod
    def validate_password_strength(cls, v: str) -> str:
        if len(v) < 8:
            raise ValueError('Password must be at least 8 characters')
        if not any(c.isupper() for c in v):
            raise ValueError('Password must contain uppercase letter')
        if not any(c.isdigit() for c in v):
            raise ValueError('Password must contain digit')
        return v
    
    @model_validator(mode='after')
    def validate_passwords_match(self):
        if self.password != self.confirm_password:
            raise ValueError('Passwords do not match')
        return self
```

## Configuration

```python
# Use ConfigDict for model configuration
class UserResponse(BaseModel):
    id: int
    email: str
    
    model_config = ConfigDict(
        from_attributes=True,  # Allow ORM model conversion
        json_schema_extra={    # OpenAPI examples
            "example": {
                "id": 1,
                "email": "user@example.com"
            }
        }
    )
```

## Rules

1. **Naming**: `*Create` for creation, `*Update` for updates, `*Response` for responses
2. **Inheritance**: Use base schemas for common fields
3. **Validation**: Use validators for complex validation logic
4. **ORM**: Set `from_attributes=True` for ORM model conversion
5. **Optional**: Use `| None` with default `None` for optional fields
