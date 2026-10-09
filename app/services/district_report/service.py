"""Orchestrating service for district analytics reports.

Ties together retrieval, writing, and PDF rendering. Used by both the
Celery task (API) and the CLI script so the report output is identical.
"""

from __future__ import annotations

import asyncio
import logging
import re
import unicodedata
from datetime import UTC, datetime
from typing import Any

from app.db.connector import AsyncSessionLocal
from app.services.district_report.pdf import render_report_pdf
from app.services.district_report.queries import (
    RETRIEVAL_SEMANTIC,
    QuerySpec,
    default_district_org_code,
    geography_to_state,
    get_query_spec,
)
from app.services.district_report.retriever import (
    fetch_corpus_summary,
    gather_citations,
    resolve_chatbot_config_id,
    resolve_focus_district,
    run_retrieval_passes,
)
from app.services.district_report.writer import (
    build_evidence_payload,
    contains_banned_terms,
    scrub_banned_terms,
    write_report,
)

logger = logging.getLogger(__name__)


class DistrictReportService:
    """Generate stakeholder-facing PDF reports for fixed district queries."""

    def __init__(self) -> None:
        # The writer gets one retry if it leaks banned terms.
        self._writer_retries = 1

    async def generate_report(
        self,
        tenant_id: int,
        query_id: str,
        chatbot_config_id: int | None = None,
        district_org_code: str | None = None,
    ) -> dict[str, Any]:
        """Generate one PDF report for a fixed query.

        For CA (semantic) queries, analysis is pinned to a single focus
        district. Pass `district_org_code` to override the tenant default
        (Saddleback Valley USD for tenant 5).

        Returns a dict with:
          - report_id: stable per (tenant_id, query_id, compiled_date)
          - query_id, title
          - tenant_id
          - compiled_at (ISO 8601 UTC)
          - filename
          - pdf_bytes
          - focus_district (when single-district)
        """
        spec = get_query_spec(query_id, tenant_id)
        chatbot_config_id = chatbot_config_id or await resolve_chatbot_config_id(tenant_id)

        focus_district: dict[str, str] | None = None
        if spec.retrieval_mode == RETRIEVAL_SEMANTIC:
            org_code = district_org_code or default_district_org_code(tenant_id)
            if not org_code:
                raise ValueError(
                    f"Semantic query {query_id!r} for tenant {tenant_id} "
                    "requires a focus district_org_code."
                )
            focus_district = await resolve_focus_district(tenant_id, org_code)

        ranked = await run_retrieval_passes(
            spec, tenant_id, chatbot_config_id, focus_district=focus_district
        )
        citations = await gather_citations(
            spec,
            ranked,
            tenant_id,
            chatbot_config_id,
            focus_district=focus_district,
        )
        corpus_summary = await fetch_corpus_summary(
            tenant_id,
            chatbot_config_id,
            state=geography_to_state(spec.geography),
            focus_district=focus_district,
        )

        compiled_at = datetime.now(UTC)
        compiled_date = compiled_at.strftime("%Y-%m-%d")
        report_id = self._make_report_id(tenant_id, query_id, compiled_date)

        evidence = build_evidence_payload(
            spec=spec,
            corpus_summary=corpus_summary,
            ranked=ranked,
            citations=citations,
            report_id=report_id,
            compiled_date=compiled_date,
        )

        async with AsyncSessionLocal() as db:
            report_markdown = await write_report(db, chatbot_config_id, evidence)

        report_markdown = self._enforce_plain_language(report_markdown, db_placeholder=evidence)

        filename = self._make_filename(tenant_id, query_id, spec, compiled_date)
        pdf_buffer = await asyncio.to_thread(
            render_report_pdf, report_markdown, spec.title
        )

        return {
            "report_id": report_id,
            "query_id": query_id,
            "title": spec.title,
            "tenant_id": tenant_id,
            "compiled_at": compiled_at.isoformat(),
            "filename": filename,
            "pdf_bytes": pdf_buffer.getvalue(),
            "focus_district": focus_district,
        }

    def _make_report_id(self, tenant_id: int, query_id: str, compiled_date: str) -> str:
        return f"DR-{tenant_id}-{query_id}-{compiled_date}"

    def _make_filename(self, tenant_id: int, query_id: str, spec: QuerySpec, compiled_date: str) -> str:
        safe_title = self._sanitize_filename(spec.title)
        return f"{query_id}_{safe_title}_tenant{tenant_id}_{compiled_date}.pdf"

    def _sanitize_filename(self, filename: str) -> str:
        replacements = {
            "\u2011": "-",
            "\u2012": "-",
            "\u2013": "-",
            "\u2014": "-",
            "\u2015": "-",
            "\u2018": "'",
            "\u2019": "'",
            "\u201c": '"',
            "\u201d": '"',
            "\u2026": "...",
        }
        for uni, asc in replacements.items():
            filename = filename.replace(uni, asc)
        normalized = unicodedata.normalize("NFKD", filename)
        ascii_str = normalized.encode("ascii", "ignore").decode("ascii")
        ascii_str = re.sub(r"[^\w\-.]", "-", ascii_str)
        ascii_str = re.sub(r"-+", "-", ascii_str)
        ascii_str = ascii_str.strip("-")
        return ascii_str or "district-report"

    def _enforce_plain_language(
        self, report_markdown: str, db_placeholder: dict[str, Any] | None = None
    ) -> str:
        """Strip/rewrite banned terms. Retry the LLM once if needed."""
        if not contains_banned_terms(report_markdown):
            return report_markdown

        logger.warning("Report contained banned terms; scrubbing as a safety net.")
        return scrub_banned_terms(report_markdown)


district_report_service = DistrictReportService()
