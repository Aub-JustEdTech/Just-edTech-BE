---
description: Database connection and session management patterns
globs: app/db/**/*.py
alwaysApply: false
---

# Database Patterns

## Connection Management (Singleton Pattern)

```python
# app/db/connector.py
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base
from app.core.config import settings

# Create engine once
engine = create_engine(
    settings.DATABASE_URL,
    pool_pre_ping=True,      # Verify connections before using
    pool_size=10,             # Connection pool size
    max_overflow=20,          # Max connections beyond pool_size
    echo=settings.DEBUG       # Log SQL queries in debug mode
)

# Create session factory
SessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=engine
)

# Base for models
Base = declarative_base()

# Dependency for FastAPI
def get_db():
    """Database session dependency"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
```

## Base Model Pattern

```python
# app/models/base.py
from datetime import datetime
from sqlalchemy import Column, Integer, DateTime
from sqlalchemy.sql import func
from app.db.connector import Base

class BaseModel(Base):
    """Base model with common fields"""
    
    __abstract__ = True
    
    id = Column(Integer, primary_key=True, index=True)
    created_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False
    )
    updated_at = Column(
        DateTime(timezone=True),
        onupdate=func.now()
    )
```

## Model Definition

```python
# app/models/users.py
from sqlalchemy import Column, String, Boolean
from app.models.base import BaseModel

class User(BaseModel):
    __tablename__ = "users"
    
    email = Column(String, unique=True, index=True, nullable=False)
    hashed_password = Column(String, nullable=False)
    full_name = Column(String)
    is_active = Column(Boolean, default=True)
```

## Session Usage Rules

1. **Always** use `Depends(get_db)` in endpoints
2. **Never** create sessions manually in endpoints
3. **Always** let dependency handle session lifecycle
4. **Use** context managers for manual session creation

```python
# ✅ GOOD - Dependency injection
@router.get("/users")
def get_users(db: Session = Depends(get_db)):
    return db.query(User).all()

# ❌ BAD - Manual session management
@router.get("/users")
def get_users():
    db = SessionLocal()
    users = db.query(User).all()
    db.close()
    return users
```

## Transaction Management

```python
# For complex operations, use explicit transactions
def create_user_with_profile(db: Session, user_data: dict) -> User:
    try:
        user = User(**user_data)
        db.add(user)
        db.flush()  # Get user.id without committing
        
        profile = Profile(user_id=user.id)
        db.add(profile)
        
        db.commit()
        return user
    except Exception:
        db.rollback()
        raise
```
