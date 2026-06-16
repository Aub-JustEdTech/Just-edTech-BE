"""
Monthly Billing model for storing monthly aggregated billing data.
"""

from sqlalchemy import (
    DECIMAL,
    BigInteger,
    Column,
    Date,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship

from app.models.base import BaseModel


class MonthlyBilling(BaseModel):
    """
    Model for storing monthly aggregated billing data.
    One record per tenant per model per month.
    """

    __tablename__ = "monthly_billing"

    # Year and month of the billing period
    billing_year = Column(Integer, nullable=False, index=True)
    billing_month = Column(Integer, nullable=False, index=True)  # 1-12

    # Start and end dates of the billing period
    period_start_date = Column(Date, nullable=False)
    period_end_date = Column(Date, nullable=False)

    # Foreign keys
    tenant_id = Column(
        BigInteger,
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Model used for the tokens
    model_name = Column(String(100), nullable=False, index=True)

    # Aggregated token counts for the month
    total_input_tokens = Column(BigInteger, nullable=False, default=0)
    total_output_tokens = Column(BigInteger, nullable=False, default=0)
    total_tokens = Column(BigInteger, nullable=False, default=0)
    total_cache_tokens = Column(BigInteger, nullable=False, default=0)

    # Count of messages processed in the month
    message_count = Column(BigInteger, nullable=False, default=0)

    # Cost calculations (in USD) for the month
    input_token_cost = Column(
        DECIMAL(12, 6),
        nullable=False,
        default=0,
        comment="Cost for input tokens in USD",
    )
    output_token_cost = Column(
        DECIMAL(12, 6),
        nullable=False,
        default=0,
        comment="Cost for output tokens in USD",
    )
    cache_token_cost = Column(
        DECIMAL(12, 6),
        nullable=False,
        default=0,
        comment="Cost for cache tokens in USD",
    )
    total_cost = Column(
        DECIMAL(12, 6),
        nullable=False,
        default=0,
        comment="Total cost for the month in USD",
    )

    # Average pricing used for the month (captured at aggregation time)
    avg_input_token_price = Column(
        DECIMAL(10, 6), nullable=True, comment="Average price per 1M input tokens"
    )
    avg_output_token_price = Column(
        DECIMAL(10, 6), nullable=True, comment="Average price per 1M output tokens"
    )
    avg_cache_token_price = Column(
        DECIMAL(10, 6), nullable=True, comment="Average price per 1M cache tokens"
    )

    # Relationships
    tenant = relationship("Tenant", back_populates="monthly_billing")

    # Ensure one record per tenant per model per month
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "model_name",
            "billing_year",
            "billing_month",
            name="uq_tenant_model_month",
        ),
        Index(
            "idx_monthly_billing_lookup", "tenant_id", "billing_year", "billing_month"
        ),
        Index("idx_monthly_billing_date_range", "period_start_date", "period_end_date"),
    )

    def __repr__(self):
        return (
            f"<MonthlyBilling(year={self.billing_year}, month={self.billing_month}, "
            f"tenant_id={self.tenant_id}, model={self.model_name}, total_cost=${self.total_cost})>"
        )
