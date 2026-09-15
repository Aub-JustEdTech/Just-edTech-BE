"""District analytics report generation for the fixed Q1-Q7 golden queries.

This package builds stakeholder-facing PDF reports by reusing the existing
district-analytics retrieval tools (`count_districts_by_topic`,
`get_district_citations`, `list_districts`) rather than running the full
agentic RAG chat agent. Each fixed query has a curated set of retrieval
passes; a single LLM call then writes the inverted-pyramid report, and
WeasyPrint renders it to PDF.
"""

from app.services.district_report.service import district_report_service

__all__ = ["district_report_service"]
