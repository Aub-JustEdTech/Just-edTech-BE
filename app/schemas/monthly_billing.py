"""
Pydantic schemas for monthly billing endpoints.
"""

from datetime import date
from decimal import Decimal

from pydantic import BaseModel, Field


class MonthlyBillingBase(BaseModel):
    """Base schema for monthly billing"""

    billing_year: int = Field(..., ge=2020, le=2100, description="Billing year")
    billing_month: int = Field(..., ge=1, le=12, description="Billing month (1-12)")
    model_name: str = Field(..., max_length=100, description="Model name")
    total_input_tokens: int = Field(default=0, ge=0, description="Total input tokens")
    total_output_tokens: int = Field(default=0, ge=0, description="Total output tokens")
    total_cache_tokens: int = Field(default=0, ge=0, description="Total cache tokens")
    total_tokens: int = Field(default=0, ge=0, description="Total tokens")
    message_count: int = Field(default=0, ge=0, description="Number of messages")


class MonthlyBillingCreate(MonthlyBillingBase):
    """Schema for creating monthly billing record"""

    tenant_id: int
    period_start_date: date
    period_end_date: date
    input_token_cost: Decimal = Field(
        default=Decimal("0.00"), description="Input token cost in USD"
    )
    output_token_cost: Decimal = Field(
        default=Decimal("0.00"), description="Output token cost in USD"
    )
    cache_token_cost: Decimal = Field(
        default=Decimal("0.00"), description="Cache token cost in USD"
    )
    total_cost: Decimal = Field(
        default=Decimal("0.00"), description="Total cost in USD"
    )


class MonthlyBillingResponse(MonthlyBillingBase):
    """Schema for monthly billing response"""

    id: int
    tenant_id: int
    period_start_date: date
    period_end_date: date
    input_token_cost: float = Field(..., description="Input token cost in USD")
    output_token_cost: float = Field(..., description="Output token cost in USD")
    cache_token_cost: float = Field(..., description="Cache token cost in USD")
    total_cost: float = Field(..., description="Total cost in USD")
    avg_input_token_price: float | None = Field(
        None, description="Average price per 1M input tokens"
    )
    avg_output_token_price: float | None = Field(
        None, description="Average price per 1M output tokens"
    )
    avg_cache_token_price: float | None = Field(
        None, description="Average price per 1M cache tokens"
    )

    class Config:
        from_attributes = True


class ModelBillingSummary(BaseModel):
    """Summary of billing for a specific model"""

    input_tokens: int
    output_tokens: int
    cache_tokens: int
    total_tokens: int
    message_count: int
    input_cost: float
    output_cost: float
    cache_cost: float
    total_cost: float


class BillingTotals(BaseModel):
    """Total billing across all models"""

    input_tokens: int
    output_tokens: int
    cache_tokens: int
    total_tokens: int
    message_count: int
    input_cost: float
    output_cost: float
    cache_cost: float
    total_cost: float


class MonthlyBillingSummaryResponse(BaseModel):
    """Schema for monthly billing summary"""

    by_model: dict[str, ModelBillingSummary]
    totals: BillingTotals
    billing_period: dict[str, int]  # {"year": 2024, "month": 10}


class YearlyBillingMonth(BaseModel):
    """Monthly data in yearly billing summary"""

    month: int
    total_tokens: int
    total_cost: float


class YearlyBillingTotals(BaseModel):
    """Yearly totals"""

    total_tokens: int
    total_cost: float


class YearlyBillingSummaryResponse(BaseModel):
    """Schema for yearly billing summary"""

    tenant_id: int
    year: int
    monthly_breakdown: dict[int, YearlyBillingMonth]
    yearly_totals: YearlyBillingTotals


class AggregateMonthlyBillingRequest(BaseModel):
    """Request schema for manually triggering monthly billing aggregation"""

    year: int | None = Field(
        None, ge=2020, le=2100, description="Billing year (defaults to previous month)"
    )
    month: int | None = Field(
        None, ge=1, le=12, description="Billing month (defaults to previous month)"
    )


class AggregateMonthlyBillingResponse(BaseModel):
    """Response schema for monthly billing aggregation task acknowledgment"""

    task_id: str = Field(..., description="Celery task ID for tracking")
    status: str = Field(..., description="Task submission status")
    message: str = Field(..., description="Human-readable status message")


class AggregateMonthlyBillingResult(BaseModel):
    """Response schema for completed monthly billing aggregation"""

    billing_year: int
    billing_month: int
    period_start: str
    period_end: str
    records_created: int
    records_updated: int
    total_records: int
    total_monthly_cost: float
