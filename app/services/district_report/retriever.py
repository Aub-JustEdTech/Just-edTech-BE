"""Retrieval layer for district analytics reports.

Reuses the existing agentic-RAG tools — same filter surface, same tenant
scoping — but calls them directly with a `RunnableConfig` instead of going
through the chat agent.

Two retrieval modes:
  - topic_counts (MA): `count_districts_by_topic` + `get_district_citations`
  - semantic (CA): embedding search over the fixed question text, pinned
    to one focus district (single-district qualitative analysis).
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import exists, select

from app.db.connector import AsyncSessionLocal
from app.models.chatbot_configs import ChatbotConfig
from app.models.school import School, SchoolScrapeUrl
from app.services.agentic_rag.tools import (
    count_districts_by_topic,
    get_district_citations,
    search_knowledge_base,
)
from app.services.district_report.queries import (
    RETRIEVAL_SEMANTIC,
    QuerySpec,
    geography_to_state,
    resolve_filters,
    resolve_search_query,
)

logger = logging.getLogger(__name__)

# How many districts to drill into with citations per retrieval pass.
TOP_DISTRICTS_FOR_CITATIONS = 5
CITATIONS_PER_DISTRICT = 4

# Single-district semantic mode: deeper evidence from one district.
SEMANTIC_TOP_K = 40
SEMANTIC_HITS_PER_DISTRICT = 12
SEMANTIC_CITATIONS_PER_DISTRICT = 10


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


async def resolve_focus_district(
    tenant_id: int,
    org_code: str,
) -> dict[str, str]:
    """Resolve an active school by org_code within the tenant.

    Returns ``{org_code, district_name, state}``. Raises ValueError if
    the district is missing or inactive for this tenant.
    """
    async with AsyncSessionLocal() as db:
        stmt = select(School).where(
            School.tenant_id == tenant_id,
            School.org_code == org_code,
            School.is_active.is_(True),
        )
        school = (await db.execute(stmt)).scalar_one_or_none()

    if school is None:
        raise ValueError(
            f"No active school with org_code={org_code!r} for tenant_id={tenant_id}."
        )
    return {
        "org_code": school.org_code or org_code,
        "district_name": school.name,
        "state": school.state or "",
    }


def _config(tenant_id: int, chatbot_config_id: int) -> dict[str, Any]:
    return {
        "configurable": {"tenant_id": tenant_id, "chatbot_config_id": chatbot_config_id}
    }


async def _safe_invoke(tool, args: dict[str, Any], config: dict[str, Any]) -> Any:
    """Invoke a tool and normalize errors into an empty result."""
    try:
        return await tool.ainvoke(args, config=config)
    except (
        Exception
    ) as exc:  # noqa: BLE001 — surface as a logged error, never crash the report
        logger.error("Retrieval tool failed: %s", exc, exc_info=True)
        return []


def _drop_none(args: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in args.items() if v is not None}


async def run_retrieval_passes(
    spec: QuerySpec,
    tenant_id: int,
    chatbot_config_id: int,
    focus_district: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    """Run retrieval for the query and return ranked district rows.

    In semantic mode each row may carry a transient `_semantic_hits`
    list that `gather_citations` consumes (then strips) so we do not
    re-embed the question. `focus_district` pins CA reports to one
    district (required for semantic mode).
    """
    if spec.retrieval_mode == RETRIEVAL_SEMANTIC:
        if focus_district is None:
            raise ValueError(
                "Semantic retrieval requires a focus_district "
                "(single-district CA reports)."
            )
        return await _run_semantic_retrieval(
            spec, tenant_id, chatbot_config_id, focus_district
        )
    return await _run_topic_count_retrieval(spec, tenant_id, chatbot_config_id)


async def _run_topic_count_retrieval(
    spec: QuerySpec,
    tenant_id: int,
    chatbot_config_id: int,
) -> list[dict[str, Any]]:
    """MA-style: merge `count_districts_by_topic` passes by org_code."""
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
            if existing is None or row.get("chunk_count", 0) > existing.get(
                "chunk_count", 0
            ):
                merged[org] = row

    ranked = sorted(
        merged.values(),
        key=lambda r: r.get("chunk_count", 0),
        reverse=True,
    )

    _annotate_passes(ranked, per_pass_rows)
    return ranked


async def _run_semantic_retrieval(
    spec: QuerySpec,
    tenant_id: int,
    chatbot_config_id: int,
    focus_district: dict[str, str],
) -> list[dict[str, Any]]:
    """CA-style: semantic search pinned to one focus district.

    Each ranked row keeps `_semantic_hits` for `gather_citations` to
    convert into citation dicts without a second embedding call.
    """
    config = _config(tenant_id, chatbot_config_id)
    state = focus_district.get("state") or geography_to_state(spec.geography)
    district_name = focus_district["district_name"]
    org_code = focus_district["org_code"]
    filter_sets = resolve_filters(spec)

    # document_id+chunk_index → best hit across passes
    hit_map: dict[tuple[Any, Any], dict[str, Any]] = {}

    for pass_idx, filters in enumerate(filter_sets):
        pass_filters = dict(filters)
        search_text = resolve_search_query(spec, pass_filters)
        pass_filters.pop("_search_query", None)
        args = _drop_none(
            {
                "query": search_text,
                "top_k": SEMANTIC_TOP_K,
                "districts": [district_name],
                "states": pass_filters.get("states") or [state],
                "meeting_date_from": pass_filters.get("meeting_date_from"),
                "meeting_date_to": pass_filters.get("meeting_date_to"),
                "meeting_doc_types": pass_filters.get("meeting_doc_types"),
                # Qualitative CA questions need the full corpus, not only
                # chunks tagged by the MA policy taxonomy.
                "require_classified": False,
            }
        )
        hits = await _safe_invoke(search_knowledge_base, args, config)
        if not isinstance(hits, list):
            continue
        for hit in hits:
            if not isinstance(hit, dict):
                continue
            key = (hit.get("document_id"), hit.get("chunk_index"))
            existing = hit_map.get(key)
            if existing is None or float(hit.get("score") or 0) > float(
                existing.get("score") or 0
            ):
                enriched = dict(hit)
                enriched["retrieval_pass"] = pass_idx
                enriched.setdefault("district_name", district_name)
                hit_map[key] = enriched

    hits = sorted(
        hit_map.values(),
        key=lambda h: float(h.get("score") or 0),
        reverse=True,
    )[:SEMANTIC_HITS_PER_DISTRICT]

    if not hits:
        return []

    return [
        {
            "org_code": org_code,
            "district_name": district_name,
            "state": state,
            "chunk_count": len(hits),
            "retrieval_pass": hits[0].get("retrieval_pass", 0),
            "_semantic_hits": hits,
        }
    ]


def _hit_to_citation(hit: dict[str, Any]) -> dict[str, Any]:
    text = hit.get("text") or hit.get("snippet") or ""
    snippet = text[:500] + ("…" if len(text) > 500 else "")
    return {
        "document_id": hit.get("document_id"),
        "document_db_id": hit.get("document_db_id"),
        "document_name": hit.get("document_name", ""),
        "document_type": hit.get("document_type", ""),
        "meeting_date": hit.get("meeting_date"),
        "meeting_doc_type": hit.get("meeting_doc_type"),
        "page_number": hit.get("page_number"),
        "chunk_index": hit.get("chunk_index", 0),
        "snippet": snippet,
        "source_media_url": hit.get("source_media_url") or "",
        "source_page_url": hit.get("source_page_url") or "",
        "action_stage": hit.get("action_stage"),
    }


def _citations_from_semantic_ranked(
    ranked: list[dict[str, Any]],
    top_n: int = TOP_DISTRICTS_FOR_CITATIONS,
    citations_per_district: int = SEMANTIC_CITATIONS_PER_DISTRICT,
) -> list[dict[str, Any]]:
    """Build citation payloads from stashed semantic hits, then clear them."""
    citations: list[dict[str, Any]] = []
    for row in ranked[:top_n]:
        hits = row.get("_semantic_hits") or []
        if not hits:
            continue
        citations.append(
            {
                "org_code": row.get("org_code"),
                "district_name": row.get("district_name"),
                "state": row.get("state"),
                "total": len(hits),
                "citations": [
                    _hit_to_citation(h) for h in hits[:citations_per_district]
                ],
            }
        )
    for row in ranked:
        row.pop("_semantic_hits", None)
    return citations


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
    focus_district: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    """Fetch citations for the top-N districts.

    Semantic mode uses hits stashed by `run_retrieval_passes` (or a
    fresh single-district search if the stash is missing). Topic-count
    mode drills into each district with `get_district_citations`.
    """
    if not ranked:
        return []

    if spec.retrieval_mode == RETRIEVAL_SEMANTIC:
        if any(row.get("_semantic_hits") for row in ranked):
            return _citations_from_semantic_ranked(ranked, top_n=top_n)
        if focus_district is None:
            return []
        fresh = await _run_semantic_retrieval(
            spec, tenant_id, chatbot_config_id, focus_district
        )
        return _citations_from_semantic_ranked(fresh, top_n=top_n)

    filter_sets = resolve_filters(spec)
    citations: list[dict[str, Any]] = []

    for row in ranked[:top_n]:
        org_code = row.get("org_code")
        if org_code is None:
            continue
        pass_idx = row.get("retrieval_pass", 0)
        filters = (
            filter_sets[pass_idx] if pass_idx < len(filter_sets) else filter_sets[0]
        )
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
    state: str,
    focus_district: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Fetch a stakeholder-facing corpus summary (district count + roster).

    When `focus_district` is set (CA single-district reports), the
    summary is that one district only — not the full state roster.

    For multi-district reports, ``district_count`` is the Confirmed Source
    total: active schools (public + charter) that have at least one active
    ``school_scrape_urls`` row. That matches the Source URL Manager badge
    and is what reports mean by "active districts" — not every school row
    on file.
    """
    if focus_district is not None:
        return {
            "district_count": 1,
            "state": focus_district.get("state") or state,
            "districts": [
                {
                    "org_code": focus_district["org_code"],
                    "district_name": focus_district["district_name"],
                    "state": focus_district.get("state") or state,
                }
            ],
            "focus_district": focus_district,
        }

    # chatbot_config_id kept for call-site parity with other retriever helpers.
    _ = chatbot_config_id
    has_confirmed_source = exists(
        select(1).where(
            SchoolScrapeUrl.school_id == School.id,
            SchoolScrapeUrl.is_active.is_(True),
        )
    )
    async with AsyncSessionLocal() as db:
        stmt = (
            select(School)
            .where(
                School.tenant_id == tenant_id,
                School.is_active.is_(True),
                School.state == state,
                has_confirmed_source,
            )
            .order_by(School.name)
        )
        schools = list((await db.execute(stmt)).scalars().all())

    districts = [
        {
            "org_code": s.org_code,
            "district_name": s.name,
            "state": s.state or state,
            "district_type": s.district_type,
        }
        for s in schools
    ]
    return {
        "district_count": len(districts),
        "state": state,
        "districts": districts,
    }
