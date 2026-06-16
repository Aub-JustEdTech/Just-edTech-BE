"""
Feedback model per ERD.
"""

from sqlalchemy import BigInteger, Boolean, Column, ForeignKey, Text
from sqlalchemy.orm import relationship

from app.models.base import BaseModel


class Feedback(BaseModel):
    __tablename__ = "feedback"

    id = Column(BigInteger, primary_key=True, index=True)
    c_id = Column(
        BigInteger, ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False
    )
    messages = Column(Text, nullable=True)
    feedback = Column(Text, nullable=True)
    is_positive = Column(Boolean, default=None, nullable=True)

    conversation = relationship("Conversation", back_populates="feedback")
