"""Retrieval layer for district analytics reports.

Reuses the existing `count_districts_by_topic` and `get_district_citations`
tools from the agentic RAG package — same filter surface, same tenant
scoping — but calls them directly with a `RunnableConfig` instead of going
through the chat agent. Each fixed query produces one or more retrieval
passes; we keep the largest result set per query for citations.
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select

from app.db.connector import AsyncSessionLocal
from app.models.chatbot_configs import ChatbotConfig
from app.services.agentic_rag.tools import (
    count_districts_by_topic,
    get_district_citations,
    list_districts,
)
from app.services.district_report.queries import QuerySpec, resolve_filters

logger = logging.getLogger(__name__)

# How many districts to drill into with citations per retrieval pass.
TOP_DISTRICTS_FOR_CITATIONS = 5
CITATIONS_PER_DISTRICT = 4


async def resolve_chatbot_config_id(tenant_id: int) -> int:
    """Resolve a chatbot_config_id for the tenant.

    The retrieval tools require both `tenant_id` and `chatbot_config_id`
    in the `RunnableConfig`, even though the vectors are scoped only by
    `tenant_id`. We pick the tenant's default chatbot config (falling back
    to the most recently created) so report generation does not require a
    chatbot_config_id to be supplied by the caller.
    """
    async with AsyncSessionLocal() as db:
        # Default first, then latest created as a fallback.
        stmt = (
            select(ChatbotConfig.id)
            .where(ChatbotConfig.tenant_id == tenant_id)
            .order_by(
                ChatbotConfig.is_default.desc(),
                ChatbotConfig.created_at.desc(),
            )
            .limit(1)
        )
        row = (await db.execute(stmt)).scalar_one_or_none()

    if row is None:
        raise ValueError(
            f"Tenant {tenant_id} has no chatbot config; cannot resolve a "
            "chatbot_config_id for retrieval."
        )
    return int(row)


def _config(tenant_id: int, chatbot_config_id: int) -> dict[str, Any]:
    return {"configurable": {"tenant_id": tenant_id, "chatbot_config_id": chatbot_config_id}}


async def _safe_invoke(tool, args: dict[str, Any], config: dict[str, Any]) -> Any:
    """Invoke a tool and normalize errors into an empty result."""
    try:
        return await tool.ainvoke(args, config=config)
    except Exception as exc:  # noqa: BLE001 — surface as a logged error, never crash the report
        logger.error("Retrieval tool failed: %s", exc, exc_info=True)
        return []


async def run_retrieval_passes(
    spec: QuerySpec,
    tenant_id: int,
    chatbot_config_id: int,
) -> list[dict[str, Any]]:
    """Run every retrieval pass for the query and return ranked district rows.

    Each pass produces a `count_districts_by_topic` result. We merge all
    passes (deduplicating by `org_code`, keeping the highest count) so the
    writer sees one ranked list per query rather than one per pass.
    """
    config = _config(tenant_id, chatbot_config_id)
    filter_sets = resolve_filters(spec)

    merged: dict[str, dict[str, Any]] = {}
    per_pass_rows: list[list[dict[str, Any]]] = []

    for filters in filter_sets:
        rows = await _safe_invoke(count_districts_by_topic, filters, config)
        if not rows or (isinstance(rows, list) and rows and "error" in rows[0]):
            per_pass_rows.append([])
            continue
        per_pass_rows.append(rows)
        for row in rows:
            if not isinstance(row, dict) or "error" in row:
                continue
            org = row.get("org_code")
            if org is None:
                continue
            existing = merged.get(org)
            if existing is None or row.get("chunk_count", 0) > existing.get("chunk_count", 0):
                merged[org] = row

    ranked = sorted(
        merged.values(),
        key=lambda r: r.get("chunk_count", 0),
        reverse=True,
    )

    # Annotate each row with the pass index that produced it (for citations).
    _annotate_passes(ranked, per_pass_rows)
    return ranked


def _annotate_passes(
    ranked: list[dict[str, Any]],
    per_pass_rows: list[list[dict[str, Any]]],
) -> None:
    # Track which pass produced the highest count for each org, so
    # citations use the filter set that had the most evidence.
    org_to_best_pass: dict[str, int] = {}
    org_to_best_count: dict[str, int] = {}
    for pass_idx, rows in enumerate(per_pass_rows):
        for row in rows:
            if not isinstance(row, dict) or row.get("org_code") is None:
                continue
            org = row["org_code"]
            count = row.get("chunk_count", 0)
            if org not in org_to_best_count or count > org_to_best_count[org]:
                org_to_best_count[org] = count
                org_to_best_pass[org] = pass_idx
    for row in ranked:
        row["retrieval_pass"] = org_to_best_pass.get(row.get("org_code"), 0)


async def fetch_citations_for_district(
    org_code: str,
    filters: dict[str, Any],
    tenant_id: int,
    chatbot_config_id: int,
    page_size: int = CITATIONS_PER_DISTRICT,
) -> dict[str, Any]:
    """Fetch the most recent citations for one district + filter set."""
    config = _config(tenant_id, chatbot_config_id)
    args = {
        "org_code": org_code,
        **filters,
        "page_size": page_size,
        "sort": "date_desc",
    }
    return await _safe_invoke(get_district_citations, args, config)


async def gather_citations(
    spec: QuerySpec,
    ranked: list[dict[str, Any]],
    tenant_id: int,
    chatbot_config_id: int,
    top_n: int = TOP_DISTRICTS_FOR_CITATIONS,
) -> list[dict[str, Any]]:
    """Fetch citations for the top-N districts, using the pass that ranked them."""
    if not ranked:
        return []

    filter_sets = resolve_filters(spec)
    citations: list[dict[str, Any]] = []

    for row in ranked[:top_n]:
        org_code = row.get("org_code")
        if org_code is None:
            continue
        pass_idx = row.get("retrieval_pass", 0)
        filters = filter_sets[pass_idx] if pass_idx < len(filter_sets) else filter_sets[0]
        resp = await fetch_citations_for_district(
            org_code=org_code,
            filters=filters,
            tenant_id=tenant_id,
            chatbot_config_id=chatbot_config_id,
        )
        if not isinstance(resp, dict) or resp.get("error"):
            continue
        citations.append(resp)
    return citations


async def fetch_corpus_summary(
    tenant_id: int,
    chatbot_config_id: int,
    state: str = "MA",
) -> dict[str, Any]:
    """Fetch a stakeholder-facing corpus summary (district count + roster).

    Returns the number of active districts and a small roster sample — no
    chunk/document counts (those are internal terms).
    """
    config = _config(tenant_id, chatbot_config_id)
    districts = await _safe_invoke(list_districts, {"state": state}, config)
    if not isinstance(districts, list):
        districts = []
    return {
        "district_count": len(districts),
        "state": state,
        "districts": districts,
    }
