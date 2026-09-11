"""Tesseract OCR provider (local, free)."""

from __future__ import annotations

import io
import logging

from app.core.config import settings
from app.services.document_processing.ocr.base import BaseOCRProvider

logger = logging.getLogger(__name__)


class TesseractOCRProvider(BaseOCRProvider):
    """OCR via system tesseract + pytesseract."""

    def ocr_image(self, image_bytes: bytes) -> str:
        try:
            import pytesseract
            from PIL import Image
        except ImportError as exc:
            raise ImportError(
                "pytesseract and Pillow are required for Tesseract OCR. "
                "Install with: poetry add pytesseract"
            ) from exc

        try:
            image = Image.open(io.BytesIO(image_bytes))
            text = pytesseract.image_to_string(
                image,
                lang=settings.OCR_LANGUAGES,
                timeout=settings.OCR_TIMEOUT_SECONDS,
            )
            return (text or "").strip()
        except Exception as exc:
            logger.warning("Tesseract OCR failed for image: %s", exc)
            return ""

    def get_provider_name(self) -> str:
        return "tesseract"
