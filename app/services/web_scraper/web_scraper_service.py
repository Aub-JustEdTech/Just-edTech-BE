"""
Web scraping service for fetching and processing web content.
"""

import logging
from typing import Any

import httpx
from pydantic import AnyHttpUrl, TypeAdapter, ValidationError

from app.core.config import settings

logger = logging.getLogger(__name__)


class WebScraperService:
    """Service for scraping web content with async HTTP client"""

    def __init__(self, timeout: int | None = None, verify_ssl: bool = True):
        """
        Initialize web scraper service.

        Args:
            timeout: Request timeout in seconds (defaults to settings.WEB_SCRAPER_TIMEOUT_SECONDS or 30)
            verify_ssl: Whether to verify SSL certificates (default: True)
        """
        self.timeout = timeout or getattr(settings, "WEB_SCRAPER_TIMEOUT_SECONDS", 30)
        self.verify_ssl = verify_ssl

        # Create TypeAdapter once for URL validation (Pydantic v2)
        self._url_adapter = TypeAdapter(AnyHttpUrl)

        # Create async HTTP client with configuration
        self.client = httpx.AsyncClient(
            timeout=httpx.Timeout(self.timeout, connect=10.0),
            verify=verify_ssl,
            follow_redirects=True,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
            },
        )

    async def __aenter__(self):
        """Async context manager entry"""
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit - close HTTP client"""
        await self.close()

    async def close(self):
        """Close the HTTP client"""
        await self.client.aclose()

    def validate_url(self, url: str) -> str:
        """
        Validate and normalize URL.

        Args:
            url: URL string to validate

        Returns:
            Validated URL string

        Raises:
            ValueError: If URL is invalid
        """
        if not url or not isinstance(url, str):
            raise ValueError("URL must be a non-empty string")

        url = url.strip()

        # Add protocol if missing
        if not url.startswith(("http://", "https://")):
            url = f"https://{url}"

        # Validate using Pydantic's AnyHttpUrl with TypeAdapter (Pydantic v2)
        try:
            validated_url = self._url_adapter.validate_python(url)
            return str(validated_url)
        except ValidationError as e:
            raise ValueError(f"Invalid URL format: {url}") from e

    async def fetch_content(self, url: str) -> dict[str, Any]:
        """
        Fetch HTML content from URL with comprehensive error handling.

        Args:
            url: URL to fetch

        Returns:
            Dictionary with 'html', 'status_code', 'headers', 'url' (final URL after redirects)

        Raises:
            ValueError: For invalid URLs
            httpx.TimeoutException: For timeout errors
            httpx.HTTPStatusError: For HTTP errors (404, 403, 500, etc.)
            httpx.NetworkError: For network errors (DNS failure, connection errors)
            httpx.RequestError: For other request errors
        """
        # Validate URL
        validated_url = self.validate_url(url)

        logger.info(f"Fetching content from URL: {validated_url}")

        try:
            response = await self.client.get(validated_url)

            # Check for rate limiting (429)
            if response.status_code == 429:
                retry_after = response.headers.get("Retry-After", "unknown")
                error_msg = (
                    f"Rate limited (429) for URL {validated_url}. "
                    f"Retry-After: {retry_after}"
                )
                logger.warning(error_msg)
                raise httpx.HTTPStatusError(
                    error_msg,
                    request=response.request,
                    response=response,
                )

            # Raise exception for HTTP errors (4xx, 5xx)
            response.raise_for_status()

            # Check if content is HTML
            content_type = response.headers.get("content-type", "").lower()
            if (
                "text/html" not in content_type
                and "application/xhtml+xml" not in content_type
            ):
                logger.warning(
                    f"Content type is not HTML for URL {validated_url}: {content_type}"
                )

            # Check for empty content
            if not response.text or len(response.text.strip()) == 0:
                logger.warning(f"Empty content received from URL: {validated_url}")

            logger.info(
                f"Successfully fetched content from {validated_url} "
                f"(status: {response.status_code}, size: {len(response.text)} bytes)"
            )

            return {
                "html": response.text,
                "status_code": response.status_code,
                "headers": dict(response.headers),
                "url": str(response.url),  # Final URL after redirects
            }

        except httpx.TimeoutException:
            error_msg = (
                f"Timeout while fetching URL {validated_url} (timeout: {self.timeout}s)"
            )
            logger.error(error_msg, exc_info=True)
            raise

        except httpx.HTTPStatusError as e:
            status_code = e.response.status_code if e.response else "unknown"
            error_msg = (
                f"HTTP error {status_code} while fetching URL {validated_url}. "
                f"Response: {e.response.text[:200] if e.response else 'N/A'}"
            )
            logger.error(error_msg, exc_info=True)
            raise

        except httpx.NetworkError as e:
            error_msg = (
                f"Network error while fetching URL {validated_url}. " f"Error: {str(e)}"
            )
            logger.error(error_msg, exc_info=True)
            raise

        except httpx.RequestError as e:
            error_msg = f"Request error while fetching URL {validated_url}: {str(e)}"
            logger.error(error_msg, exc_info=True)
            raise

        except Exception as e:
            error_msg = f"Unexpected error while fetching URL {validated_url}: {str(e)}"
            logger.error(error_msg, exc_info=True)
            raise RuntimeError(error_msg) from e
