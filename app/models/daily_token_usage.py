"""
Daily Token Usage model for storing aggregated token statistics.
"""

from sqlalchemy import (
    DECIMAL,
    BigInteger,
    Column,
    Date,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship

from app.models.base import BaseModel


class DailyTokenUsage(BaseModel):
    """
    Model for storing daily aggregated token usage statistics.
    One record per tenant per model per day.
    """

    __tablename__ = "daily_token_usage"

    # Date of the aggregated data (stored as date, not datetime)
    usage_date = Column(Date, nullable=False, index=True)

    # Foreign keys
    tenant_id = Column(
        BigInteger,
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Model used for the tokens
    model_name = Column(String(100), nullable=False, index=True)

    # Aggregated token counts
    total_input_tokens = Column(BigInteger, nullable=False, default=0)
    total_output_tokens = Column(BigInteger, nullable=False, default=0)
    total_tokens = Column(BigInteger, nullable=False, default=0)

    # Cache tokens (for models that support caching)
    total_cache_tokens = Column(BigInteger, nullable=False, default=0)

    # Count of messages processed
    message_count = Column(BigInteger, nullable=False, default=0)

    # Cost calculations (in USD)
    input_token_cost = Column(
        DECIMAL(12, 6), nullable=True, default=0, comment="Cost for input tokens in USD"
    )
    output_token_cost = Column(
        DECIMAL(12, 6),
        nullable=True,
        default=0,
        comment="Cost for output tokens in USD",
    )
    cache_token_cost = Column(
        DECIMAL(12, 6), nullable=True, default=0, comment="Cost for cache tokens in USD"
    )
    total_cost = Column(
        DECIMAL(12, 6), nullable=True, default=0, comment="Total cost in USD"
    )

    # Relationships
    tenant = relationship("Tenant", back_populates="daily_token_usage")

    # Ensure one record per tenant per model per date
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "model_name", "usage_date", name="uq_tenant_model_date"
        ),
        Index("idx_daily_usage_lookup", "tenant_id", "usage_date", "model_name"),
    )

    def __repr__(self):
        return (
            f"<DailyTokenUsage(date={self.usage_date}, tenant_id={self.tenant_id}, "
            f"model={self.model_name}, total_tokens={self.total_tokens})>"
        )
