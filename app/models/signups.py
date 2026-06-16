"""
Signup model for pre-tenant registrations.
Stores user details and desired tenant info until email verification completes.
"""

from sqlalchemy import Boolean, Column, DateTime, String

from app.models.base import BaseModel


class Signup(BaseModel):
    __tablename__ = "signups"

    # User details
    email = Column(String, unique=True, index=True, nullable=False)
    name = Column(String, nullable=True)
    password_hash = Column(String, nullable=False)

    # Optional expiry if needed later
    expires_at = Column(DateTime, nullable=True)

    # Email verification status in signup stage
    is_verified = Column(Boolean, default=False, nullable=False)
    verified_at = Column(DateTime, nullable=True)
