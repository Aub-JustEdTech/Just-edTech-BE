"""Factory for document processors"""

import logging
from pathlib import Path

from app.services.document_processing.base import DocumentProcessor
from app.services.document_processing.processors.doc_processor import DocProcessor
from app.services.document_processing.processors.docx_processor import DocxProcessor
from app.services.document_processing.processors.markdown_processor import (
    MarkdownProcessor,
)
from app.services.document_processing.processors.pdf_processor import PDFProcessor
from app.services.document_processing.processors.xlsx_processor import XLSXProcessor

logger = logging.getLogger(__name__)


class ProcessorFactory:
    """Factory for creating document processors based on file type"""

    _processors: dict[str, type[DocumentProcessor]] = {
        ".pdf": PDFProcessor,
        ".md": MarkdownProcessor,
        ".markdown": MarkdownProcessor,
        ".txt": MarkdownProcessor,
        ".text": MarkdownProcessor,
        ".docx": DocxProcessor,
        ".doc": DocProcessor,
        ".xlsx": XLSXProcessor,
        ".xls": XLSXProcessor,
    }

    @classmethod
    def get_processor(cls, file_path: str) -> DocumentProcessor:
        """Get appropriate processor for file type"""
        extension = Path(file_path).suffix.lower()

        processor_class = cls._processors.get(extension)
        if not processor_class:
            raise ValueError(f"Unsupported file type: {extension}")

        logger.debug(f"Selected {processor_class.__name__} for {extension}")
        return processor_class()

    @classmethod
    def register_processor(
        cls, extension: str, processor_class: type[DocumentProcessor]
    ):
        """Register new processor for extension"""
        cls._processors[extension] = processor_class
        logger.info(f"Registered {processor_class.__name__} for {extension}")

    @classmethod
    def get_supported_extensions(cls) -> list[str]:
        """Get list of supported file extensions"""
        return list(cls._processors.keys())
