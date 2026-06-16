"""
CRUD operations for User model.
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import settings
from app.models.users import User
from app.schemas.users import UserCreate, UserUpdate
from app.utils.auth import get_password_hash, verify_password


class UserCRUD:
    """CRUD operations for User model"""

    async def get(self, db: AsyncSession, user_id: int) -> User | None:
        """Get user by ID with relationships loaded"""
        result = await db.execute(
            select(User)
            .options(selectinload(User.role), selectinload(User.tenant))
            .where(User.id == user_id)
        )
        return result.scalar_one_or_none()

    async def get_by_email(self, db: AsyncSession, email: str) -> User | None:
        """Get user by email with relationships loaded"""
        result = await db.execute(
            select(User)
            .options(selectinload(User.role), selectinload(User.tenant))
            .where(User.email == email)
        )
        return result.scalar_one_or_none()

    async def create(self, db: AsyncSession, user_create: UserCreate) -> User:
        """Create new user"""
        password_hash = get_password_hash(user_create.password)
        db_user = User(
            email=user_create.email,
            name=user_create.name,
            password_hash=password_hash,
            tenant_id=user_create.tenant_id
            if user_create.tenant_id
            else settings.DEFAULT_TENANT_ID,
            role_id=user_create.role_id
            if user_create.role_id
            else settings.DEFAULT_ROLE_ID,  # Default to 'tenant_user' role
        )
        db.add(db_user)
        await db.commit()
        await db.refresh(db_user)
        return await self.get(db, db_user.id)  # Return with relationships loaded

    async def update(
        self, db: AsyncSession, user_id: int, user_update: UserUpdate
    ) -> User | None:
        """Update user"""
        db_user = await self.get(db, user_id)
        if not db_user:
            return None

        update_data = user_update.dict(exclude_unset=True)
        for field, value in update_data.items():
            setattr(db_user, field, value)

        await db.commit()
        await db.refresh(db_user)
        return await self.get(db, user_id)  # Return with relationships loaded

    async def authenticate(
        self, db: AsyncSession, email: str, password: str
    ) -> User | None:
        """Authenticate user by email and password"""
        user_obj = await self.get_by_email(db, email)
        if not user_obj:
            return None
        if not verify_password(password, user_obj.password_hash):
            return None
        return user_obj

    def get_role_name(self, user: User) -> str | None:
        """Get user's role name"""
        return user.role.name if user.role else None

    def get_tenant_name(self, user: User) -> str | None:
        """Get user's tenant name"""
        return user.tenant.name if user.tenant else None

    def has_role(self, user: User, role_name: str) -> bool:
        """Check if user has specific role"""
        return user.role and user.role.name == role_name

    def is_super_admin(self, user: User) -> bool:
        """Check if user is super admin"""
        return self.has_role(user, "super_admin")

    def is_tenant_admin(self, user: User) -> bool:
        """Check if user is tenant admin"""
        return self.has_role(user, "tenant_admin")

    def is_tenant_user(self, user: User) -> bool:
        """Check if user is regular tenant user"""
        return self.has_role(user, "tenant_user")


user = UserCRUD()
