"""Tests for Playwright CMS folder interactions (SharpSchool explorers)."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from app.services.web_scraper.playwright_interactions import (
    folder_may_contain_allowed_years,
    is_sharpschool_getfile_url,
    looks_like_sharpschool_document_list,
)
from app.services.web_scraper.school_scraper_service import SchoolScraperService


def test_looks_like_sharpschool_document_list():
    assert looks_like_sharpschool_document_list(
        '<ul class="documentList" id="documentList"></ul>'
    )
    assert looks_like_sharpschool_document_list(
        'ContentItemModern/scripts/ContentItemFolder.js'
    )
    assert looks_like_sharpschool_document_list(
        'href="/common/pages/GetFile.ashx?key=abc"'
    )
    assert not looks_like_sharpschool_document_list("<html><body>plain</body></html>")


def test_is_sharpschool_getfile_url():
    assert is_sharpschool_getfile_url(
        "https://www.leicester.k12.ma.us/common/pages/GetFile.ashx?key=LegOBNwT"
    )
    assert not is_sharpschool_getfile_url("https://example.com/minutes.pdf")


@pytest.mark.parametrize(
    ("name", "allowed", "expected"),
    [
        ("2024-2025 School Committee Meeting Minutes", {2023, 2024, 2025, 2026}, True),
        ("2022-2023 School Committee Minutes", {2023, 2024, 2025, 2026}, True),
        ("2021-2022 School Committee Minutes", {2023, 2024, 2025, 2026}, False),
        ("2019-2020 School Committee Minutes", {2023, 2024, 2025, 2026}, False),
        ("School Committee Minutes", {2023, 2024, 2025, 2026}, True),
        ("Archived School Committee Minutes", {2023, 2024, 2025, 2026}, True),
    ],
)
def test_folder_may_contain_allowed_years(name, allowed, expected):
    with patch(
        "app.services.web_scraper.playwright_interactions.allowed_calendar_years",
        return_value=allowed,
    ):
        assert folder_may_contain_allowed_years(name) is expected


def test_match_file_extension_recognizes_getfile_ashx():
    svc = SchoolScraperService()
    assert (
        svc._match_file_extension(
            "/common/pages/getfile.ashx",
            {".pdf", ".docx"},
        )
        == ".pdf"
    )
    assert (
        svc._match_file_extension(
            "/common/pages/getfile.ashx",
            {".pdf", ".docx"},
            filename_hint="March Minutes.docx",
        )
        == ".docx"
    )


def test_extract_media_includes_getfile_ashx_links():
    html = """
    <html><body>
      <a class="content_item" href="/common/pages/GetFile.ashx?key=abc">
        <div class="docTitle">03-03-2025 Minutes</div>
      </a>
    </body></html>
    """
    svc = SchoolScraperService()
    media, _ = svc._extract_media_from_page(
        html,
        "https://www.leicester.k12.ma.us/SC/minutes",
        video_ext=[".mp4"],
        audio_ext=[".mp3"],
        doc_ext=[".pdf", ".docx"],
    )
    assert len(media) == 1
    assert "GetFile.ashx" in media[0]["url"]
    assert media[0]["media_type"] == "document"
    assert "03-03-2025" in (media[0]["name"] or "")
