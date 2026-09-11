"""
Schema-driven school-site crawler POC.

Experiment branch only. The canonical implementation now lives in
app/services/web_scraper/ (page_schemas, page_classifier, schema_driven_crawler).
This package re-exports those symbols so the POC scripts (run_poc, compare)
and the eval harness keep importing from the same place without drift.
"""

from app.services.web_scraper.page_classifier import PageClassifier
from app.services.web_scraper.page_schemas import (
    DATA_TYPES,
    DataPageInfo,
    PossibleRelevantPage,
    RelevantPage,
)
from app.services.web_scraper.schema_driven_crawler import (
    CrawlResult,
    SchemaDrivenCrawler,
)

__all__ = [
    "CrawlResult",
    "DATA_TYPES",
    "DataPageInfo",
    "PageClassifier",
    "PossibleRelevantPage",
    "RelevantPage",
    "SchemaDrivenCrawler",
]
