"""
API key model per ERD.
"""

from sqlalchemy import BigInteger, Column, ForeignKey, String
from sqlalchemy.orm import relationship

from app.models.base import BaseModel


class ApiKey(BaseModel):
    __tablename__ = "api_keys"

    id = Column(BigInteger, primary_key=True, index=True)
    tenant_id = Column(
        BigInteger, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    key = Column(String, nullable=False, unique=True, index=True)

    tenant = relationship("Tenant", back_populates="api_keys")
