---
description: Repository pattern for data access abstraction
globs: app/crud/**/*.py
alwaysApply: false
---

# Repository Pattern

Use Repository pattern in `app/crud/` to abstract data access.

## Structure

```python
# app/crud/users.py
from sqlalchemy.orm import Session
from app.models.users import User

def get_by_id(db: Session, user_id: int) -> User | None:
    """Get user by ID"""
    return db.query(User).filter(User.id == user_id).first()

def get_by_email(db: Session, email: str) -> User | None:
    """Get user by email"""
    return db.query(User).filter(User.email == email).first()

def create(db: Session, user_data: dict) -> User:
    """Create new user"""
    db_user = User(**user_data)
    db.add(db_user)
    db.commit()
    db.refresh(db_user)
    return db_user

def update(db: Session, user: User, update_data: dict) -> User:
    """Update existing user"""
    for key, value in update_data.items():
        setattr(user, key, value)
    db.commit()
    db.refresh(user)
    return db_user

def delete(db: Session, user: User) -> None:
    """Delete user"""
    db.delete(user)
    db.commit()
```

## Rules

1. **Pure Data Access**: No business logic, only database operations
2. **Return Models**: Return SQLAlchemy models or None, not dictionaries
3. **Session Parameter**: Always accept `db: Session` as first parameter
4. **Type Hints**: Always use type hints for parameters and return values
5. **Docstrings**: Add brief docstrings for each function
6. **No Exceptions**: Let database errors propagate, handle in service layer

## Common Operations

```python
# Get single record
def get_by_field(db: Session, field_value: Any) -> Model | None:
    return db.query(Model).filter(Model.field == field_value).first()

# Get multiple records
def get_all(db: Session, skip: int = 0, limit: int = 100) -> list[Model]:
    return db.query(Model).offset(skip).limit(limit).all()

# Get with filters
def get_filtered(db: Session, **filters) -> list[Model]:
    query = db.query(Model)
    for key, value in filters.items():
        query = query.filter(getattr(Model, key) == value)
    return query.all()

# Count records
def count(db: Session, **filters) -> int:
    query = db.query(Model)
    for key, value in filters.items():
        query = query.filter(getattr(Model, key) == value)
    return query.count()
```
