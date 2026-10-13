"""Unit tests for PDF OCR fallback (Phase 1).

Run:
    poetry run pytest tests/test_pdf_ocr.py -v
"""

from __future__ import annotations

from unittest.mock import patch

import fitz
import pytest

from app.services.document_processing.ocr.factory import OCRProviderFactory
from app.services.document_processing.ocr.tesseract_provider import (
    TesseractOCRProvider,
)
from app.services.document_processing.processors.pdf_processor import PDFProcessor


def _make_text_pdf(path: str, text: str) -> None:
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), text)
    doc.save(path)
    doc.close()


def _make_blank_pdf(path: str, pages: int = 1) -> None:
    doc = fitz.open()
    for _ in range(pages):
        doc.new_page()
    doc.save(path)
    doc.close()


class _FakeOCR:
    def __init__(self, text: str = "OCR recovered text from scan"):
        self.text = text
        self.calls = 0

    def ocr_image(self, image_bytes: bytes) -> str:
        self.calls += 1
        assert image_bytes  # rendered page must be non-empty
        return self.text

    def get_provider_name(self) -> str:
        return "fake"


def test_factory_creates_tesseract():
    provider = OCRProviderFactory.create("tesseract")
    assert isinstance(provider, TesseractOCRProvider)
    assert provider.get_provider_name() == "tesseract"


def test_factory_rejects_unknown_provider():
    with pytest.raises(ValueError, match="Unsupported OCR provider"):
        OCRProviderFactory.create("not-a-provider")


def test_digital_text_skips_ocr(tmp_path):
    pdf_path = str(tmp_path / "digital.pdf")
    _make_text_pdf(pdf_path, "Plenty of digital text in this board minutes document.")

    fake = _FakeOCR()
    processor = PDFProcessor()

    with (
        patch(
            "app.services.document_processing.processors.pdf_processor.settings.ENABLE_OCR",
            True,
        ),
        patch(
            "app.services.document_processing.processors.pdf_processor.settings.OCR_MIN_CHARS_THRESHOLD",
            50,
        ),
        patch(
            "app.services.document_processing.ocr.factory.OCRProviderFactory.create",
            return_value=fake,
        ),
    ):
        pages = processor.extract_text_by_page(pdf_path)

    assert fake.calls == 0
    assert processor.ocr_used is False
    assert any("digital text" in p.lower() for p in pages)


def test_empty_digital_triggers_ocr(tmp_path):
    pdf_path = str(tmp_path / "scanned.pdf")
    _make_blank_pdf(pdf_path, pages=2)

    fake = _FakeOCR("Recovered OCR content")
    processor = PDFProcessor()

    with (
        patch(
            "app.services.document_processing.processors.pdf_processor.settings.ENABLE_OCR",
            True,
        ),
        patch(
            "app.services.document_processing.processors.pdf_processor.settings.OCR_MIN_CHARS_THRESHOLD",
            50,
        ),
        patch(
            "app.services.document_processing.processors.pdf_processor.settings.OCR_MAX_PAGES",
            100,
        ),
        patch(
            "app.services.document_processing.processors.pdf_processor.settings.OCR_DPI",
            150,
        ),
        patch(
            "app.services.document_processing.ocr.factory.OCRProviderFactory.create",
            return_value=fake,
        ),
    ):
        pages = processor.extract_text_by_page(pdf_path)

    assert fake.calls == 2
    assert processor.ocr_used is True
    assert processor.ocr_pages_count == 2
    assert all(p == "Recovered OCR content" for p in pages)


def test_ocr_disabled_leaves_empty(tmp_path):
    pdf_path = str(tmp_path / "scanned.pdf")
    _make_blank_pdf(pdf_path, pages=1)

    fake = _FakeOCR()
    processor = PDFProcessor()

    with (
        patch(
            "app.services.document_processing.processors.pdf_processor.settings.ENABLE_OCR",
            False,
        ),
        patch(
            "app.services.document_processing.ocr.factory.OCRProviderFactory.create",
            return_value=fake,
        ),
    ):
        pages = processor.extract_text_by_page(pdf_path)

    assert fake.calls == 0
    assert processor.ocr_used is False
    assert pages == [""]


def test_ocr_respects_max_pages(tmp_path):
    pdf_path = str(tmp_path / "long_scan.pdf")
    _make_blank_pdf(pdf_path, pages=5)

    fake = _FakeOCR("page text")
    processor = PDFProcessor()

    with (
        patch(
            "app.services.document_processing.processors.pdf_processor.settings.ENABLE_OCR",
            True,
        ),
        patch(
            "app.services.document_processing.processors.pdf_processor.settings.OCR_MIN_CHARS_THRESHOLD",
            50,
        ),
        patch(
            "app.services.document_processing.processors.pdf_processor.settings.OCR_MAX_PAGES",
            2,
        ),
        patch(
            "app.services.document_processing.processors.pdf_processor.settings.OCR_DPI",
            100,
        ),
        patch(
            "app.services.document_processing.ocr.factory.OCRProviderFactory.create",
            return_value=fake,
        ),
    ):
        pages = processor.extract_text_by_page(pdf_path)

    assert fake.calls == 2
    assert processor.ocr_pages_count == 2
    assert pages[0] == "page text"
    assert pages[1] == "page text"
    assert pages[2] == ""
    assert pages[3] == ""
    assert pages[4] == ""


def test_should_apply_ocr_threshold():
    with (
        patch(
            "app.services.document_processing.processors.pdf_processor.settings.ENABLE_OCR",
            True,
        ),
        patch(
            "app.services.document_processing.processors.pdf_processor.settings.OCR_MIN_CHARS_THRESHOLD",
            50,
        ),
    ):
        assert PDFProcessor._should_apply_ocr([""]) is True
        assert PDFProcessor._should_apply_ocr(["x" * 49]) is True
        assert PDFProcessor._should_apply_ocr(["x" * 50]) is False
