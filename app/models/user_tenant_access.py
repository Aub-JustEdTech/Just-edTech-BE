"""
Join table granting a user access to a specific tenant.
Used for super_admin (bypasses all checks) and tenant_admin (one row per tenant).
"""

from sqlalchemy import BigInteger, Column, DateTime, ForeignKey, UniqueConstraint
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.models.base import Base


class UserTenantAccess(Base):
    __tablename__ = "user_tenant_access"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    user_id = Column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    tenant_id = Column(
        BigInteger, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    created_at = Column(DateTime, nullable=True, server_default=func.now())

    __table_args__ = (
        UniqueConstraint("user_id", "tenant_id", name="uq_user_tenant_access"),
    )

    user = relationship("User", back_populates="tenant_access")
    tenant = relationship("Tenant", back_populates="tenant_access")
