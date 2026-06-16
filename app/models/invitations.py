"""
Invitation model per ERD.
"""

from sqlalchemy import BigInteger, Boolean, Column, ForeignKey, String
from sqlalchemy.orm import relationship

from app.models.base import BaseModel


class Invitation(BaseModel):
    __tablename__ = "invitations"

    id = Column(BigInteger, primary_key=True, index=True)
    tenant_id = Column(
        BigInteger, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    email = Column(String, nullable=False, index=True)
    role_id = Column(
        BigInteger, ForeignKey("roles.id", ondelete="SET NULL"), nullable=True
    )
    token = Column(String, unique=True, nullable=False, index=True)
    accepted = Column(Boolean, default=False, nullable=False)

    tenant = relationship("Tenant", back_populates="invitations")
    role = relationship("Role")
