"""Base OCR provider interface."""

from abc import ABC, abstractmethod


class BaseOCRProvider(ABC):
    """Abstract base class for OCR providers."""

    @abstractmethod
    def ocr_image(self, image_bytes: bytes) -> str:
        """
        Extract text from a single page/image.

        Args:
            image_bytes: Encoded image bytes (PNG or JPEG).

        Returns:
            Extracted text (may be empty on failure).
        """

    @abstractmethod
    def get_provider_name(self) -> str:
        """Return the provider identifier (e.g. 'tesseract')."""
