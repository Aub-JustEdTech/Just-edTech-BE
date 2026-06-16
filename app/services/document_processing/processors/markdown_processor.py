"""Markdown document processor"""

import logging
from typing import Any

from app.services.document_processing.base import DocumentProcessor

logger = logging.getLogger(__name__)


class MarkdownProcessor(DocumentProcessor):
    """Process Markdown documents"""

    supported_extensions = [".md", ".markdown", ".txt", ".text"]
    supported_mime_types = ["text/markdown", "text/plain"]

    def extract_text(self, file_path: str) -> str:
        """Extract text from Markdown"""
        try:
            with open(file_path, encoding="utf-8") as file:
                text = file.read()
            logger.info(f"Extracted {len(text)} characters from Markdown")
            return text.strip()
        except Exception as e:
            logger.error(f"Error extracting text from Markdown: {e}")
            raise

    def extract_metadata(self, file_path: str) -> dict[str, Any]:
        """Extract Markdown metadata"""
        try:
            with open(file_path, encoding="utf-8") as file:
                text = file.read()

            # Count lines and words
            lines = text.count("\n") + 1
            words = len(text.split())

            return {
                "line_count": lines,
                "word_count": words,
                "character_count": len(text),
            }
        except Exception as e:
            logger.error(f"Error extracting Markdown metadata: {e}")
            return {}

    def validate(self, file_path: str) -> bool:
        """Validate Markdown file"""
        try:
            with open(file_path, encoding="utf-8") as file:
                file.read()
            return True
        except Exception:
            return False
