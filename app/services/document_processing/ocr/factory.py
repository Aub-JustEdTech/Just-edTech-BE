"""Factory for OCR provider instances."""

from __future__ import annotations

import logging

from app.core.config import settings
from app.services.document_processing.ocr.base import BaseOCRProvider
from app.services.document_processing.ocr.tesseract_provider import TesseractOCRProvider

logger = logging.getLogger(__name__)


class OCRProviderFactory:
    """Factory for creating OCR provider instances from configuration."""

    _providers: dict[str, type[BaseOCRProvider]] = {
        "tesseract": TesseractOCRProvider,
    }

    @classmethod
    def create(cls, provider_name: str | None = None) -> BaseOCRProvider:
        name = (provider_name or settings.OCR_PROVIDER).lower().strip()
        provider_class = cls._providers.get(name)
        if provider_class is None:
            raise ValueError(
                f"Unsupported OCR provider: {name}. "
                f"Supported: {list(cls._providers.keys())}"
            )
        logger.debug("Creating OCR provider: %s", name)
        return provider_class()

    @classmethod
    def get_available_providers(cls) -> list[str]:
        return list(cls._providers.keys())
