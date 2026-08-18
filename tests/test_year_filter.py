"""Unit tests for app.services.web_scraper.year_filter.

Run:
    poetry run pytest tests/test_year_filter.py -v
"""

from __future__ import annotations

from datetime import date
from unittest.mock import patch

from app.services.web_scraper.year_filter import (
    evaluate_media_year,
    evaluate_media_year_async,
    filter_media_files,
    filter_media_files_async,
    is_meeting_date_in_range,
    should_crawl_page_url,
)

ALLOWED = [2023, 2024, 2025, 2026]


@patch(
    "app.services.web_scraper.year_filter.settings.SCHOOL_SCRAPER_ALLOWED_YEARS",
    ALLOWED,
)
@patch(
    "app.services.web_scraper.year_filter.settings.SCHOOL_SCRAPER_DOWNLOAD_ON_UNKNOWN_YEAR",
    False,
)
def test_evaluate_rejects_out_of_range_year():
    year, ok, reason = evaluate_media_year(
        url="https://example.com/board/2021/minutes.pdf",
        filename=None,
        source_page_url=None,
    )
    assert year == 2021
    assert ok is False
    assert reason is not None
    assert "2021" in reason


@patch(
    "app.services.web_scraper.year_filter.settings.SCHOOL_SCRAPER_ALLOWED_YEARS",
    ALLOWED,
)
@patch(
    "app.services.web_scraper.year_filter.settings.SCHOOL_SCRAPER_DOWNLOAD_ON_UNKNOWN_YEAR",
    False,
)
def test_evaluate_accepts_allowed_year():
    year, ok, reason = evaluate_media_year(
        url="https://example.com/board/2024/minutes.pdf",
        filename=None,
        source_page_url=None,
    )
    assert year == 2024
    assert ok is True
    assert reason is None


@patch(
    "app.services.web_scraper.year_filter.settings.SCHOOL_SCRAPER_ALLOWED_YEARS",
    ALLOWED,
)
@patch(
    "app.services.web_scraper.year_filter.settings.SCHOOL_SCRAPER_DOWNLOAD_ON_UNKNOWN_YEAR",
    False,
)
def test_evaluate_rejects_unknown_year_when_flag_false():
    year, ok, reason = evaluate_media_year(
        url="https://example.com/fs/resource-manager/view/abc-uuid",
        filename="minutes.pdf",
        source_page_url="https://example.com/board/meetings",
    )
    assert year is None
    assert ok is False
    assert reason == "year could not be inferred"


@patch(
    "app.services.web_scraper.year_filter.settings.SCHOOL_SCRAPER_ALLOWED_YEARS",
    ALLOWED,
)
@patch(
    "app.services.web_scraper.year_filter.settings.SCHOOL_SCRAPER_DOWNLOAD_ON_UNKNOWN_YEAR",
    True,
)
def test_evaluate_allows_unknown_year_when_flag_true():
    year, ok, reason = evaluate_media_year(
        url="https://example.com/fs/resource-manager/view/abc-uuid",
        filename="minutes.pdf",
        source_page_url="https://example.com/board/meetings",
    )
    assert year is None
    assert ok is True
    assert reason is None


@patch(
    "app.services.web_scraper.year_filter.settings.SCHOOL_SCRAPER_ALLOWED_YEARS",
    ALLOWED,
)
def test_should_crawl_skips_out_of_range_archive_pages():
    assert should_crawl_page_url("https://example.com/minutes/2021/") is False
    assert should_crawl_page_url("https://example.com/minutes/2024/") is True
    assert should_crawl_page_url("https://example.com/board/meetings") is True


@patch(
    "app.services.web_scraper.year_filter.settings.SCHOOL_SCRAPER_ALLOWED_YEARS",
    ALLOWED,
)
@patch(
    "app.services.web_scraper.year_filter.settings.SCHOOL_SCRAPER_DOWNLOAD_ON_UNKNOWN_YEAR",
    False,
)
def test_filter_media_files_drops_out_of_range():
    media = [
        {
            "url": "https://example.com/2021/a.pdf",
            "name": "a.pdf",
            "source_page_url": "https://example.com/2021/",
        },
        {
            "url": "https://example.com/2024/b.pdf",
            "name": "b.pdf",
            "source_page_url": "https://example.com/2024/",
        },
    ]
    kept = filter_media_files(media)
    assert len(kept) == 1
    assert kept[0]["url"].endswith("2024/b.pdf")


@patch(
    "app.services.web_scraper.year_filter.settings.SCHOOL_SCRAPER_ALLOWED_YEARS",
    ALLOWED,
)
def test_meeting_date_in_range():
    assert is_meeting_date_in_range(date(2024, 3, 14)) is True
    assert is_meeting_date_in_range(date(2022, 11, 1)) is False
    assert is_meeting_date_in_range(None) is False


@patch(
    "app.services.web_scraper.year_filter.settings.SCHOOL_SCRAPER_ALLOWED_YEARS",
    ALLOWED,
)
@patch(
    "app.services.web_scraper.year_filter.settings.SCHOOL_SCRAPER_DOWNLOAD_ON_UNKNOWN_YEAR",
    False,
)
@patch(
    "app.services.web_scraper.year_filter.settings.SCHOOL_SCRAPER_YOUTUBE_TRANSCRIPT_ENABLED",
    False,
)
async def test_evaluate_async_skips_ytdlp_when_youtube_transcript_disabled():
    with patch(
        "app.services.transcription.youtube.fetch_youtube_upload_year"
    ) as mock_fetch:
        year, ok, reason = await evaluate_media_year_async(
            url="https://www.youtube.com/watch?v=abc12345678",
            filename=None,
            source_page_url="https://example.com/board",
        )
    mock_fetch.assert_not_called()
    assert year is None
    assert ok is False
    assert reason == "year could not be inferred"


@patch(
    "app.services.web_scraper.year_filter.settings.SCHOOL_SCRAPER_YOUTUBE_TRANSCRIPT_ENABLED",
    False,
)
async def test_filter_media_files_async_skips_youtube_when_transcript_disabled():
    with patch(
        "app.services.web_scraper.year_filter.evaluate_media_year_async"
    ) as mock_eval:
        mock_eval.return_value = (2024, True, None)
        kept = await filter_media_files_async(
            [
                {
                    "url": "https://www.youtube.com/watch?v=abc12345678",
                    "name": "Board meeting",
                },
                {
                    "url": "https://example.com/2024/minutes.pdf",
                    "name": "minutes.pdf",
                },
            ]
        )
    mock_eval.assert_awaited_once()
    assert len(kept) == 1
    assert kept[0]["url"].endswith("minutes.pdf")
