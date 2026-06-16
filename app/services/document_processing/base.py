"""
Abstract base class for document processors.
"""

from abc import ABC, abstractmethod
from typing import Any


class DocumentProcessor(ABC):
    """Abstract interface for document processing"""

    supported_extensions: list[str] = []
    supported_mime_types: list[str] = []

    @abstractmethod
    def extract_text(self, file_path: str) -> str:
        """Extract text content from document"""
        pass

    @abstractmethod
    def extract_metadata(self, file_path: str) -> dict[str, Any]:
        """Extract document metadata (pages, author, etc.)"""
        pass

    @abstractmethod
    def validate(self, file_path: str) -> bool:
        """Validate if file can be processed"""
        pass
