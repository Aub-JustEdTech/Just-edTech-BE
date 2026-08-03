"""
Web scraping services for extracting content from web pages.
"""

from app.services.web_scraper.markdown_converter import MarkdownConverter
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
from app.services.web_scraper.school_scraper_service import SchoolScraperService
from app.services.web_scraper.web_scraper_service import WebScraperService

__all__ = [
    "CrawlResult",
    "DATA_TYPES",
    "DataPageInfo",
    "MarkdownConverter",
    "PageClassifier",
    "PossibleRelevantPage",
    "RelevantPage",
    "SchemaDrivenCrawler",
    "SchoolScraperService",
    "WebScraperService",
]
