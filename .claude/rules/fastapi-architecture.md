---
description: FastAPI architecture patterns and layer separation
globs: app/**/*.py
alwaysApply: false
---

# FastAPI Architecture & Layer Separation

## Layer Hierarchy

Follow strict layer separation:

```
API Endpoints (app/api/endpoints/)
    ↓
Services (app/services/)
    ↓
CRUD/Repository (app/crud/)
    ↓
Models (app/models/)
    ↓
Database
```

## Rules

### 1. Endpoints Layer (`app/api/endpoints/`)
- **Only handle**: HTTP requests/responses, validation, authentication
- **Must use**: Pydantic schemas for request/response
- **Must inject**: Dependencies via `Depends()`
- **Never**: Direct database queries, business logic

```python
# ✅ GOOD
@router.post("/users", response_model=UserResponse)
def create_user(
    user: UserCreate,
    db: Session = Depends(get_db),
    user_service: UserService = Depends(get_user_service)
):
    return user_service.create_user(db, user)

# ❌ BAD - Business logic in endpoint
@router.post("/users")
def create_user(user: UserCreate, db: Session = Depends(get_db)):
    hashed_pw = bcrypt.hash(user.password)
    db_user = User(email=user.email, hashed_password=hashed_pw)
    db.add(db_user)
    db.commit()
    return db_user
```

### 2. Services Layer (`app/services/`)
- **Handle**: Business logic, orchestration, validation
- **Can call**: Multiple CRUD operations, external APIs
- **Never**: Direct SQLAlchemy queries

```python
# ✅ GOOD
class UserService:
    def create_user(self, db: Session, user_data: UserCreate) -> User:
        # Business logic
        if self._email_exists(db, user_data.email):
            raise ValueError("Email already exists")
        
        # Orchestrate operations
        user = user_crud.create(db, user_data)
        self._send_welcome_email(user.email)
        return user
```

### 3. CRUD Layer (`app/crud/`)
- **Only handle**: Database operations
- **Must be**: Pure data access, no business logic
- **Return**: Models or None

```python
# ✅ GOOD
def get_user(db: Session, user_id: int) -> User | None:
    return db.query(User).filter(User.id == user_id).first()

# ❌ BAD - Business logic in CRUD
def get_user(db: Session, user_id: int) -> User | None:
    user = db.query(User).filter(User.id == user_id).first()
    if user and not user.is_active:
        raise ValueError("User is inactive")
    return user
```

### 4. Models Layer (`app/models/`)
- **Only define**: Database schema, relationships
- **Never**: Business logic, validation (use schemas)

### 5. Schemas Layer (`app/schemas/`)
- **Define**: Request/response DTOs, validation rules
- **Use**: Pydantic validators for complex validation
