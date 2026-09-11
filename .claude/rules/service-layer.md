---
description: Service layer patterns for business logic
globs: app/services/**/*.py
alwaysApply: false
---

# Service Layer Patterns

Services contain business logic and orchestrate CRUD operations.

## Service Structure

```python
# app/services/user_service.py
from sqlalchemy.orm import Session
from app.crud import users as user_crud
from app.schemas.users import UserCreate, UserUpdate
from app.models.users import User
from app.utils.exceptions import NotFoundError, ValidationError

class UserService:
    """Service for user business logic"""
    
    def get_user(self, db: Session, user_id: int) -> User:
        """Get user by ID with validation"""
        user = user_crud.get_by_id(db, user_id)
        if not user:
            raise NotFoundError("User", user_id)
        return user
    
    def create_user(self, db: Session, user_data: UserCreate) -> User:
        """Create user with business logic"""
        # Validation
        existing = user_crud.get_by_email(db, user_data.email)
        if existing:
            raise ValidationError("Email already registered")
        
        # Create user
        user = user_crud.create(db, user_data)
        
        # Additional operations
        self._send_welcome_email(user.email)
        
        return user
    
    def update_user(self, db: Session, user_id: int, update_data: UserUpdate) -> User:
        """Update user with validation"""
        user = self.get_user(db, user_id)
        
        # Business logic
        if update_data.email and update_data.email != user.email:
            self._verify_email_available(db, update_data.email)
        
        return user_crud.update(db, user, update_data.model_dump(exclude_unset=True))
    
    def _send_welcome_email(self, email: str) -> None:
        """Private helper method"""
        # Email sending logic
        pass
    
    def _verify_email_available(self, db: Session, email: str) -> None:
        """Private validation helper"""
        if user_crud.get_by_email(db, email):
            raise ValidationError("Email already in use")
```

## Dependency Injection for Services

```python
# app/utils/dependencies.py
def get_user_service() -> UserService:
    """Dependency for UserService"""
    return UserService()

# Usage in endpoint
@router.post("/users")
def create_user(
    user: UserCreate,
    db: Session = Depends(get_db),
    service: UserService = Depends(get_user_service)
):
    return service.create_user(db, user)
```

## Facade Pattern for Complex Services

When orchestrating multiple services:

```python
# app/services/registration_service.py
class RegistrationService:
    """Facade for user registration process"""
    
    def __init__(self):
        self.user_service = UserService()
        self.email_service = EmailService()
        self.auth_service = AuthService()
    
    def register_user(self, db: Session, user_data: UserCreate) -> dict:
        """Complete registration flow"""
        # Create user
        user = self.user_service.create_user(db, user_data)
        
        # Send verification email
        token = self.auth_service.create_verification_token(user.email)
        self.email_service.send_verification(user.email, token)
        
        # Generate access token
        access_token = self.auth_service.create_access_token(user.email)
        
        return {
            "user": user,
            "access_token": access_token
        }
```

## Rules

1. **Single Responsibility**: Each service handles one domain
2. **No Direct DB Queries**: Always use CRUD layer
3. **Business Logic**: All validation and business rules go here
4. **Orchestration**: Coordinate multiple CRUD operations
5. **Private Methods**: Use `_` prefix for internal helpers
6. **Stateless**: Services should be stateless (no instance variables for data)
