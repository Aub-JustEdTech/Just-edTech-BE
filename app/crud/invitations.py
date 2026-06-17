"""
CRUD operations for Invitation model.
"""

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.invitations import Invitation


class InvitationCRUD:
    async def create(
        self,
        db: AsyncSession,
        *,
        tenant_id: int,
        email: str,
        role_id: int | None,
        token: str,
    ) -> Invitation:
        inv = Invitation(
            tenant_id=tenant_id,
            email=email,
            role_id=role_id,
            token=token,
            accepted=False,
        )
        db.add(inv)
        await db.commit()
        await db.refresh(inv)
        return inv

    async def get_by_token(self, db: AsyncSession, token: str) -> Invitation | None:
        res = await db.execute(select(Invitation).where(Invitation.token == token))
        return res.scalar_one_or_none()

    async def get_active_by_email_tenant(
        self, db: AsyncSession, *, tenant_id: int, email: str
    ) -> Invitation | None:
        res = await db.execute(
            select(Invitation)
            .where(
                and_(
                    Invitation.tenant_id == tenant_id,
                    Invitation.email == email,
                    Invitation.accepted.is_(False),
                )
            )
            .order_by(Invitation.id.desc())
        )
        return res.scalars().first()

    async def get_all_active_by_email(
        self, db: AsyncSession, email: str
    ) -> list[Invitation]:
        """Return all active invitations for an email ordered by newest first."""
        res = await db.execute(
            select(Invitation)
            .where(
                and_(
                    Invitation.email == email,
                    Invitation.accepted.is_(False),
                )
            )
            .order_by(Invitation.id.desc())
        )
        return list(res.scalars().all())

    async def mark_accepted(self, db: AsyncSession, invitation_id: int) -> None:
        res = await db.execute(select(Invitation).where(Invitation.id == invitation_id))
        inv = res.scalar_one_or_none()
        if not inv:
            return
        inv.accepted = True
        await db.commit()

    async def update_token(
        self, db: AsyncSession, invitation_id: int, token: str
    ) -> None:
        res = await db.execute(select(Invitation).where(Invitation.id == invitation_id))
        inv = res.scalar_one_or_none()
        if not inv:
            return
        inv.token = token
        inv.accepted = False
        await db.commit()


invitations = InvitationCRUD()
