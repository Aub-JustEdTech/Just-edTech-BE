"""
Web scraping services for extracting content from web pages.
"""

from app.services.web_scraper.markdown_converter import MarkdownConverter
from app.services.web_scraper.school_scraper_service import SchoolScraperService
from app.services.web_scraper.web_scraper_service import WebScraperService

__all__ = ["WebScraperService", "MarkdownConverter", "SchoolScraperService"]
