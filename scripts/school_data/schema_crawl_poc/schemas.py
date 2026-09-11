"""Pydantic schemas for the schema-driven crawler POC.

Canonical implementation now lives in
app/services/web_scraper/page_schemas.py. This module re-exports the symbols
so existing POC imports keep working without drift.
"""

from app.services.web_scraper.page_schemas import (  # noqa: F401
    DATA_TYPES,
    DataPageInfo,
    PossibleRelevantPage,
    RelevantPage,
)
