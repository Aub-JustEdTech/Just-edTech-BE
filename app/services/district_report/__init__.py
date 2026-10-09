"""District analytics report generation for fixed tenant query catalogs.

This package builds stakeholder-facing PDF reports by reusing the existing
agentic-RAG retrieval tools rather than running the full chat agent.

- Massachusetts (tenant 4): multi-district taxonomy counts + citations
- California (tenant 5): single-district semantic search (default: Saddleback)

Each fixed query has curated retrieval passes; a single LLM call then
writes the inverted-pyramid report, and WeasyPrint renders it to PDF.
"""

from app.services.district_report.service import district_report_service

__all__ = ["district_report_service"]
