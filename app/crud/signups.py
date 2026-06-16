"""
CRUD operations for Signup model.
"""

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.signups import Signup


class SignupCRUD:
    async def create(
        self,
        db: AsyncSession,
        *,
        email: str,
        name: str | None,
        password_hash: str,
    ) -> Signup:
        signup = Signup(
            email=email,
            name=name,
            password_hash=password_hash,
        )
        db.add(signup)
        await db.commit()
        await db.refresh(signup)
        return signup

    async def get_by_email(self, db: AsyncSession, email: str) -> Signup | None:
        result = await db.execute(select(Signup).where(Signup.email == email))
        return result.scalar_one_or_none()

    async def delete_by_email(self, db: AsyncSession, email: str) -> int:
        result = await db.execute(delete(Signup).where(Signup.email == email))
        await db.commit()
        return result.rowcount or 0


signup = SignupCRUD()
