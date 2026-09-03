"""Schemas for the district analytics report API."""

from __future__ import annotations

from pydantic import BaseModel, Field


class DistrictReportRequest(BaseModel):
    """Request body for POST /api/v1/district-reports."""

    query_id: str = Field(
        ...,
        description="Fixed query ID (Q1-Q7). See GET /district-reports/queries.",
    )
    tenant_id: int = Field(
        ...,
        description=(
            "Tenant ID to scope the report to. Required — the JWT claim is "
            "not used because a user may have access to multiple tenants "
            "(via user_tenant_access). super_admin bypasses the access "
            "check; tenant_admin must have a row in user_tenant_access."
        ),
    )
    chatbot_config_id: int | None = Field(
        None,
        description=(
            "Optional chatbot config to use for the writer LLM. When omitted, "
            "the tenant's default chatbot config is used."
        ),
    )


class DistrictReportTaskResponse(BaseModel):
    """Returned immediately by POST /district-reports — 202 Accepted."""

    task_id: str
    status: str = "PENDING"


class DistrictReportStatusResponse(BaseModel):
    """Poll this until `running` is False, then `download_url` / `error` is set."""

    running: bool
    task_status: str | None = None
    query_id: str | None = None
    report_id: str | None = None
    filename: str | None = None
    # Set on a task failure (worker crash, retrieval error, etc.).
    error: str | None = None


class DistrictQueryInfo(BaseModel):
    """A fixed query supported by the report API."""

    query_id: str
    title: str
    research_goal: str
    question: str
    geography: str = "Massachusetts"
