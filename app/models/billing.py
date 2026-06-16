"""
Billing model per ERD.
"""

from sqlalchemy import DECIMAL, BigInteger, Column, ForeignKey, String
from sqlalchemy.orm import relationship

from app.models.base import BaseModel


class Billing(BaseModel):
    __tablename__ = "billing"

    id = Column(BigInteger, primary_key=True, index=True)
    tenant_id = Column(
        BigInteger, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    amount = Column(DECIMAL(asdecimal=True), nullable=False)
    status = Column(String, nullable=False)

    tenant = relationship("Tenant", back_populates="billing")
