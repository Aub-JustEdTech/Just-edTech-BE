"""
Schema-driven school-site crawler POC.

Experiment branch only. Mirrors the approach from
https://noosphereanalytics.com/blog/posts/schema-driven-crawling-is-cheap-and-effective/

Reuses app.services.llm.client and app.services.web_scraper.markdown_converter
but does NOT modify any file in app/. The existing keyword-based
SchoolScraperService remains untouched and remains the production default.
"""

from scripts.school_data.schema_crawl_poc.classifier import PageClassifier
from scripts.school_data.schema_crawl_poc.crawler import SchemaDrivenCrawler
from scripts.school_data.schema_crawl_poc.schemas import (
    DataPageInfo,
    PossibleRelevantPage,
    RelevantPage,
)

__all__ = [
    "DataPageInfo",
    "PageClassifier",
    "PossibleRelevantPage",
    "RelevantPage",
    "SchemaDrivenCrawler",
]
