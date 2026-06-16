"""DOCX document processor"""

import logging
from typing import Any

try:
    from docx import Document
except ImportError:
    Document = None

from app.services.document_processing.base import DocumentProcessor

logger = logging.getLogger(__name__)


class DocxProcessor(DocumentProcessor):
    """Process DOCX (Microsoft Word) documents"""

    supported_extensions = [".docx"]
    supported_mime_types = [
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    ]

    def extract_text(self, file_path: str) -> str:
        """Extract text from DOCX"""
        if Document is None:
            raise ImportError(
                "python-docx is required for DOCX processing. Install it with: pip install python-docx"
            )

        try:
            doc = Document(file_path)
            text_parts = []

            # Extract text from paragraphs
            for paragraph in doc.paragraphs:
                if paragraph.text.strip():
                    text_parts.append(paragraph.text)

            # Extract text from tables
            for table in doc.tables:
                for row in table.rows:
                    for cell in row.cells:
                        if cell.text.strip():
                            text_parts.append(cell.text)

            text = "\n\n".join(text_parts)
            logger.info(f"Extracted {len(text)} characters from DOCX")
            return text.strip()
        except Exception as e:
            logger.error(f"Error extracting text from DOCX: {e}")
            raise

    def extract_metadata(self, file_path: str) -> dict[str, Any]:
        """Extract DOCX metadata"""
        if Document is None:
            raise ImportError(
                "python-docx is required for DOCX processing. Install it with: pip install python-docx"
            )

        try:
            doc = Document(file_path)
            core_props = doc.core_properties

            # Count paragraphs and tables
            paragraph_count = len([p for p in doc.paragraphs if p.text.strip()])
            table_count = len(doc.tables)

            # Get text for word count
            text = "\n".join([p.text for p in doc.paragraphs])
            word_count = len(text.split())

            metadata = {
                "paragraph_count": paragraph_count,
                "table_count": table_count,
                "word_count": word_count,
                "character_count": len(text),
                "author": core_props.author or "",
                "title": core_props.title or "",
                "subject": core_props.subject or "",
                "created": str(core_props.created) if core_props.created else "",
                "modified": str(core_props.modified) if core_props.modified else "",
            }
            return metadata
        except Exception as e:
            logger.error(f"Error extracting DOCX metadata: {e}")
            return {"paragraph_count": 0, "table_count": 0}

    def validate(self, file_path: str) -> bool:
        """Validate DOCX file"""
        if Document is None:
            return False

        try:
            Document(file_path)
            return True
        except Exception:
            return False
