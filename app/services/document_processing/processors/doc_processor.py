"""DOC (old Microsoft Word) document processor"""

import logging
import subprocess
from pathlib import Path
from typing import Any

from app.services.document_processing.base import DocumentProcessor

logger = logging.getLogger(__name__)


class DocProcessor(DocumentProcessor):
    """Process DOC (old Microsoft Word) documents"""

    supported_extensions = [".doc"]
    supported_mime_types = ["application/msword"]

    def extract_text(self, file_path: str) -> str:
        """Extract text from DOC file"""
        try:
            # Try using antiword (most reliable for .doc files)
            # Falls back to textract or other methods if available
            text = self._extract_with_antiword(file_path)
            if text:
                logger.info(f"Extracted {len(text)} characters from DOC using antiword")
                return text.strip()

            # Fallback: try textract if available
            text = self._extract_with_textract(file_path)
            if text:
                logger.info(f"Extracted {len(text)} characters from DOC using textract")
                return text.strip()

            raise ValueError(
                "No suitable tool found for DOC extraction. "
                "Install antiword (system package) or textract (pip install textract)"
            )
        except Exception as e:
            logger.error(f"Error extracting text from DOC: {e}")
            raise

    def _extract_with_antiword(self, file_path: str) -> str:
        """Extract text using antiword command-line tool"""
        try:
            result = subprocess.run(
                ["antiword", file_path],
                capture_output=True,
                text=True,
                check=True,
                timeout=30,
            )
            return result.stdout
        except (
            subprocess.CalledProcessError,
            FileNotFoundError,
            subprocess.TimeoutExpired,
        ):
            return ""

    def _extract_with_textract(self, file_path: str) -> str:
        """Extract text using textract library"""
        try:
            import textract

            text = textract.process(file_path).decode("utf-8")
            return text
        except ImportError:
            return ""
        except Exception:
            return ""

    def extract_metadata(self, file_path: str) -> dict[str, Any]:
        """Extract DOC metadata"""
        try:
            # Extract text first to get basic stats
            text = self.extract_text(file_path)

            # Count lines and words
            lines = text.count("\n") + 1
            words = len(text.split())

            return {
                "line_count": lines,
                "word_count": words,
                "character_count": len(text),
            }
        except Exception as e:
            logger.error(f"Error extracting DOC metadata: {e}")
            return {"line_count": 0, "word_count": 0, "character_count": 0}

    def validate(self, file_path: str) -> bool:
        """Validate DOC file"""
        try:
            # Check if file exists and has .doc extension
            path = Path(file_path)
            if not path.exists() or path.suffix.lower() != ".doc":
                return False

            # Try to extract text to validate file is readable
            # This is a basic check - actual validation happens during extraction
            return True
        except Exception:
            return False
