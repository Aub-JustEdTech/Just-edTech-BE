"""
Schemas for DailyTokenUsage API.
"""

from datetime import date

from pydantic import BaseModel


class DailyTokenUsageBase(BaseModel):
    """Base schema for daily token usage"""

    usage_date: date
    tenant_id: int
    model_name: str
    total_input_tokens: int
    total_output_tokens: int
    total_tokens: int
    total_cache_tokens: int
    message_count: int


class DailyTokenUsageResponse(DailyTokenUsageBase):
    """Response schema for daily token usage"""

    id: int
    input_token_cost: float | None = None
    output_token_cost: float | None = None
    cache_token_cost: float | None = None
    total_cost: float | None = None

    class Config:
        from_attributes = True


class DailyTokenUsageListResponse(BaseModel):
    """Response schema for list of daily token usage records"""

    items: list[DailyTokenUsageResponse]
    total: int


class UsageSummaryByModel(BaseModel):
    """Usage summary for a specific model"""

    input_tokens: int
    output_tokens: int
    total_tokens: int
    message_count: int


class UsageSummaryTotals(BaseModel):
    """Total usage summary across all models"""

    input_tokens: int
    output_tokens: int
    total_tokens: int
    message_count: int


class UsageSummaryDateRange(BaseModel):
    """Date range for usage summary"""

    start_date: str
    end_date: str


class UsageSummaryResponse(BaseModel):
    """Response schema for usage summary"""

    by_model: dict[str, UsageSummaryByModel]
    totals: UsageSummaryTotals
    date_range: UsageSummaryDateRange


class AggregationResultResponse(BaseModel):
    """Response schema for aggregation task results"""

    target_date: str
    records_created: int
    records_updated: int
    total_records: int
    total_tokens_processed: int
    total_cost_calculated: float | None = None


class BackfillResultResponse(BaseModel):
    """Response schema for backfill task results"""

    start_date: str
    end_date: str
    days_processed: int
    total_records_created: int
    total_records_updated: int
    total_tokens_processed: int
    errors: list[str]
