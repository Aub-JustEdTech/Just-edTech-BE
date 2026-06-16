"""
HTML to Markdown converter with metadata extraction.
"""

import logging
from typing import Any

import html2text
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)


class MarkdownConverter:
    """Convert HTML to Markdown format with metadata extraction"""

    def __init__(self):
        """Initialize markdown converter with html2text configuration"""
        # Configure html2text converter
        self.converter = html2text.HTML2Text()
        self.converter.ignore_links = False  # Preserve links
        self.converter.ignore_images = False  # Preserve images
        self.converter.body_width = 0  # Don't wrap lines
        self.converter.unicode_snob = True  # Use unicode characters
        self.converter.escape_snob = True  # Escape special markdown characters

    def extract_metadata(self, html: str, url: str) -> dict[str, Any]:
        """
        Extract metadata from HTML content.

        Args:
            html: HTML content string
            url: Source URL

        Returns:
            Dictionary with metadata (title, description, author, etc.)
        """
        try:
            soup = BeautifulSoup(html, "html.parser")

            # Extract title
            title = None
            if soup.title:
                title = soup.title.get_text().strip()
            elif soup.find("meta", property="og:title"):
                title = (
                    soup.find("meta", property="og:title").get("content", "").strip()
                )
            elif soup.find("h1"):
                title = soup.find("h1").get_text().strip()

            # Extract description
            description = None
            meta_desc = soup.find("meta", attrs={"name": "description"})
            if meta_desc:
                description = meta_desc.get("content", "").strip()
            elif soup.find("meta", property="og:description"):
                description = (
                    soup.find("meta", property="og:description")
                    .get("content", "")
                    .strip()
                )

            # Extract author
            author = None
            meta_author = soup.find("meta", attrs={"name": "author"})
            if meta_author:
                author = meta_author.get("content", "").strip()
            elif soup.find("meta", property="article:author"):
                author = (
                    soup.find("meta", property="article:author")
                    .get("content", "")
                    .strip()
                )

            # Remove unwanted elements before converting
            # Remove scripts, styles, navigation, footers
            for element in soup.find_all(
                ["script", "style", "nav", "footer", "header", "aside"]
            ):
                element.decompose()

            return {
                "title": title or "Untitled",
                "description": description or "",
                "author": author or "",
                "url": url,
            }

        except Exception as e:
            logger.warning(f"Error extracting metadata: {e}")
            return {
                "title": "Untitled",
                "description": "",
                "author": "",
                "url": url,
            }

    def convert_to_markdown(self, html: str) -> str:
        """
        Convert HTML to Markdown format.

        Args:
            html: HTML content string

        Returns:
            Markdown formatted string
        """
        try:
            soup = BeautifulSoup(html, "html.parser")

            # Remove unwanted elements (scripts, styles, navigation, footers)
            for element in soup.find_all(
                ["script", "style", "nav", "footer", "header", "aside"]
            ):
                element.decompose()

            # Convert to markdown
            markdown = self.converter.handle(str(soup))

            return markdown.strip()

        except Exception as e:
            logger.error(f"Error converting HTML to Markdown: {e}", exc_info=True)
            raise RuntimeError(f"Failed to convert HTML to Markdown: {e}") from e

    def format_markdown_document(
        self,
        html: str,
        url: str,
        metadata: dict[str, Any] | None = None,
        include_metadata: bool = True,
    ) -> str:
        """
        Format HTML content as a structured markdown document.

        Format (when include_metadata is True):
        1. title:
        2. Source URL:
        3. Description:
        4. content:

        Format (when include_metadata is False):
        content:

        Args:
            html: HTML content string
            url: Source URL
            metadata: Optional metadata dictionary (if None and include_metadata is True, will be extracted)
            include_metadata: Whether to include metadata sections in the output

        Returns:
            Formatted markdown document string
        """
        content = self.convert_to_markdown(html)

        # If metadata is not requested, return only content
        if not include_metadata:
            return content

        # Extract metadata if not provided
        if metadata is None:
            metadata = self.extract_metadata(html, url)

        # Format according to specification with metadata
        markdown_doc = f"""1. title:

{metadata.get('title', 'Untitled')}

2. Source URL:

{metadata.get('url', url)}

3. Description:

{metadata.get('description', '')}

4. content:

{content}
"""

        return markdown_doc.strip()
