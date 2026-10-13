"""
Dispatch helper for school URL discovery ranking modes.

Resolves SCHOOL_SCRAPER_RANKING_MODE to the right discovery service(s) and
returns a unified result dict so the discovery endpoints don't branch on the
mode themselves. `keyword` (default) → SchoolScraperService only; `llm` →
SchemaDrivenSchoolScraperService only; `both` → run both and union the
candidates (LLM candidates carry data_type/is_archive/data_years_available;
keyword candidates contribute the matched_keywords score).
"""

from __future__ import annotations

import logging
from typing import Any

from app.core.config import settings
from app.services.web_scraper.school_scraper_service import SchoolScraperService
from app.services.web_scraper.schema_driven_school_scraper_service import (
    SchemaDrivenSchoolScraperService,
)

logger = logging.getLogger(__name__)


async def discover_with_ranking_mode(
    base_url: str,
    *,
    max_candidates: int | None = None,
    use_playwright: bool | None = None,
    ranking_mode: str | None = None,
) -> dict[str, Any]:
    """Run discovery according to the configured (or overridden) ranking mode.

    Returns a dict shaped like SchoolScraperService.discover_candidate_urls'
    output, plus a `ranking_mode` key reflecting the mode that ran.
    """
    mode = (ranking_mode or settings.SCHOOL_SCRAPER_RANKING_MODE or "keyword").strip().lower()
    if mode not in {"keyword", "llm", "both"}:
        logger.warning("Unknown SCHOOL_SCRAPER_RANKING_MODE %r; falling back to keyword", mode)
        mode = "keyword"

    if mode == "keyword":
        async with SchoolScraperService(use_playwright=use_playwright or False) as svc:
            result = await svc.discover_candidate_urls(
                base_url=base_url, max_candidates=max_candidates
            )
        result["ranking_mode"] = "keyword"
        # Keyword path has no page budget; never hits a limit.
        result.setdefault("max_pages_limit_reached", False)
        return result

    if mode == "llm":
        svc = SchemaDrivenSchoolScraperService()
        result = await svc.discover_candidate_urls(
            base_url=base_url, max_candidates=max_candidates
        )
        result["ranking_mode"] = "llm"
        result.setdefault("max_pages_limit_reached", False)
        return result

    # both: run keyword + LLM and union candidates by URL, keeping LLM
    # schema fields when present and the keyword matched_keywords otherwise.
    async with SchoolScraperService(use_playwright=use_playwright or False) as kw_svc:
        kw_result = await kw_svc.discover_candidate_urls(
            base_url=base_url, max_candidates=max_candidates
        )
    llm_svc = SchemaDrivenSchoolScraperService()
    llm_result = await llm_svc.discover_candidate_urls(
        base_url=base_url, max_candidates=max_candidates
    )

    merged: dict[str, dict[str, Any]] = {}
    for c in kw_result.get("candidates", []):
        merged[c["url"]] = dict(c)
    for c in llm_result.get("candidates", []):
        if c["url"] in merged:
            # Prefer LLM schema fields; keep keyword matched_keywords/score if higher.
            base = merged[c["url"]]
            base["data_type"] = c.get("data_type") or base.get("data_type")
            base["is_archive"] = c.get("is_archive", False) or base.get("is_archive", False)
            base["data_years_available"] = c.get("data_years_available") or base.get("data_years_available", [])
            if c.get("score", 0) > base.get("score", 0):
                base["score"] = c["score"]
        else:
            merged[c["url"]] = dict(c)

    # Re-rank by score and cap.
    candidates = sorted(merged.values(), key=lambda c: c.get("score", 0), reverse=True)
    if max_candidates:
        candidates = candidates[:max_candidates]
    return {
        "base_url": kw_result.get("base_url") or base_url,
        "discovery_method": f"{kw_result.get('discovery_method', 'keyword')}+schema-driven-llm",
        "total_urls_scanned": kw_result.get("total_urls_scanned", 0)
        + llm_result.get("total_urls_scanned", 0),
        "candidates": candidates,
        "ranking_mode": "both",
        # `both` ran the LLM crawler, so its budget flag applies. Keyword has
        # no equivalent, so it cannot have hit a limit.
        "max_pages_limit_reached": bool(
            llm_result.get("max_pages_limit_reached", False)
        ),
    }
