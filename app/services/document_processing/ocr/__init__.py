"""OCR providers for scanned / image-only document text extraction."""

from app.services.document_processing.ocr.base import BaseOCRProvider
from app.services.document_processing.ocr.factory import OCRProviderFactory

__all__ = ["BaseOCRProvider", "OCRProviderFactory"]
