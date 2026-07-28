"""Schema-driven crawler POC.

Canonical implementation now lives in
app/services/web_scraper/schema_driven_crawler.py. This module re-exports
SchemaDrivenCrawler and CrawlResult so existing POC imports keep working
without drift.
"""

from app.services.web_scraper.schema_driven_crawler import (  # noqa: F401
    CrawlResult,
    SchemaDrivenCrawler,
)
