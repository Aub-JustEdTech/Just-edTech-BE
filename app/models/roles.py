"""
Role model per ERD.
"""

from sqlalchemy import BigInteger, Column, String
from sqlalchemy.orm import relationship

from app.models.base import BaseModel


class Role(BaseModel):
    __tablename__ = "roles"

    id = Column(BigInteger, primary_key=True, index=True)
    name = Column(String, unique=True, nullable=False, index=True)

    users = relationship("User", back_populates="role")
