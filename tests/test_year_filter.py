"""Unit tests for app.services.web_scraper.year_filter.

Run:
    poetry run pytest tests/test_year_filter.py -v
"""

from __future__ import annotations

from datetime import date
from unittest.mock import patch

from app.services.web_scraper.year_filter import (
    evaluate_media_cutoff,
    evaluate_media_cutoff_async,
    evaluate_media_processability_async,
    evaluate_media_year,
    evaluate_media_year_async,
    filter_media_files,
    filter_media_files_async,
    is_meeting_date_in_range,
    should_crawl_page_url,
)

ALLOWED = [2023, 2024, 2025, 2026]
CUTOFF = date(2026, 9, 1)


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


# ---------------------------------------------------------------------------
# AV date cutoff (audio/video/youtube only — documents unaffected)
# ---------------------------------------------------------------------------


@patch("app.services.web_scraper.year_filter.settings.SCHOOL_SCRAPER_MEDIA_CUTOFF_DATE", CUTOFF)
def test_cutoff_accepts_exact_date_on_or_after():
    year, ok, reason = evaluate_media_cutoff(
        url="https://example.com/files/20260901_meeting.mp4",
        filename=None,
        source_page_url=None,
    )
    assert year == 2026
    assert ok is True
    assert reason is None


@patch("app.services.web_scraper.year_filter.settings.SCHOOL_SCRAPER_MEDIA_CUTOFF_DATE", CUTOFF)
def test_cutoff_rejects_exact_date_before_cutoff():
    year, ok, reason = evaluate_media_cutoff(
        url="https://example.com/files/20260615_meeting.mp4",
        filename=None,
        source_page_url=None,
    )
    assert year == 2026
    assert ok is False
    assert reason is not None and "before the 2026-09-01 cutoff" in reason


@patch("app.services.web_scraper.year_filter.settings.SCHOOL_SCRAPER_MEDIA_CUTOFF_DATE", CUTOFF)
def test_cutoff_year_only_decisive_when_year_beats_cutoff_year():
    year, ok, reason = evaluate_media_cutoff(
        url="https://example.com/files/2027-meeting.mp4",
        filename=None,
        source_page_url=None,
    )
    assert year == 2027
    assert ok is True
    assert reason is None


@patch("app.services.web_scraper.year_filter.settings.SCHOOL_SCRAPER_MEDIA_CUTOFF_DATE", CUTOFF)
def test_cutoff_year_only_decisive_when_year_before_cutoff_year():
    year, ok, reason = evaluate_media_cutoff(
        url="https://example.com/files/2025-meeting.mp4",
        filename=None,
        source_page_url=None,
    )
    assert year == 2025
    assert ok is False
    assert reason is not None and "before the cutoff year" in reason


@patch("app.services.web_scraper.year_filter.settings.SCHOOL_SCRAPER_MEDIA_CUTOFF_DATE", CUTOFF)
def test_cutoff_ambiguous_same_year_as_cutoff_is_not_decisive():
    """A bare 2026 (no day) ties the cutoff year — could be Jan or Dec, so the
    cheap sync check can't decide and conservatively skips (the async version
    resolves this via a metadata fetch)."""
    year, ok, reason = evaluate_media_cutoff(
        url="https://example.com/files/2026-meeting.mp4",
        filename=None,
        source_page_url=None,
    )
    assert year == 2026
    assert ok is False
    assert reason is not None and "exact date is unknown" in reason


@patch("app.services.web_scraper.year_filter.settings.SCHOOL_SCRAPER_MEDIA_CUTOFF_DATE", CUTOFF)
def test_cutoff_no_date_at_all_is_not_decisive():
    year, ok, reason = evaluate_media_cutoff(
        url="https://example.com/fs/resource-manager/view/abc-uuid",
        filename=None,
        source_page_url=None,
    )
    assert year is None
    assert ok is False
    assert reason == "date could not be determined"


@patch("app.services.web_scraper.year_filter.settings.SCHOOL_SCRAPER_MEDIA_CUTOFF_DATE", None)
def test_cutoff_disabled_when_setting_unset():
    year, ok, reason = evaluate_media_cutoff(
        url="https://example.com/files/2020-old.mp4",
        filename=None,
        source_page_url=None,
    )
    assert year is None
    assert ok is True
    assert reason is None


@patch("app.services.web_scraper.year_filter.settings.SCHOOL_SCRAPER_MEDIA_CUTOFF_DATE", CUTOFF)
async def test_cutoff_async_resolves_ambiguous_year_via_youtube_metadata():
    with patch(
        "app.services.transcription.youtube.fetch_youtube_upload_date"
    ) as mock_fetch:
        mock_fetch.return_value = date(2026, 3, 1)  # before the cutoff
        year, ok, reason = await evaluate_media_cutoff_async(
            url="https://www.youtube.com/watch?v=abc12345678",
            filename=None,
            source_page_url="https://example.com/school-committee/2026-meetings",
        )
    mock_fetch.assert_awaited_once()
    assert year == 2026
    assert ok is False
    assert reason is not None and "before the 2026-09-01 cutoff" in reason


@patch("app.services.web_scraper.year_filter.settings.SCHOOL_SCRAPER_MEDIA_CUTOFF_DATE", CUTOFF)
async def test_cutoff_async_does_not_fetch_metadata_for_decisive_year():
    """A decisively out-of-range year (e.g. 2024) never needs a metadata
    round-trip — no amount of day precision changes the answer."""
    with patch(
        "app.services.transcription.youtube.fetch_youtube_upload_date"
    ) as mock_fetch:
        year, ok, reason = await evaluate_media_cutoff_async(
            url="https://www.youtube.com/watch?v=2024-old-meeting-abc",
            filename="2024-meeting.mp4",
            source_page_url=None,
        )
    mock_fetch.assert_not_called()
    assert year == 2024
    assert ok is False


@patch("app.services.web_scraper.year_filter.settings.SCHOOL_SCRAPER_MEDIA_CUTOFF_DATE", CUTOFF)
async def test_cutoff_async_falls_back_to_last_modified_for_direct_files():
    with patch(
        "app.services.web_scraper.year_filter.fetch_url_last_modified_date"
    ) as mock_fetch:
        mock_fetch.return_value = date(2026, 10, 1)  # after the cutoff
        year, ok, reason = await evaluate_media_cutoff_async(
            url="https://example.com/files/recording.mp3",
            filename=None,
            source_page_url=None,
        )
    mock_fetch.assert_awaited_once()
    assert year == 2026
    assert ok is True
    assert reason is None


@patch("app.services.web_scraper.year_filter.settings.SCHOOL_SCRAPER_MEDIA_CUTOFF_DATE", CUTOFF)
async def test_cutoff_async_unresolvable_date_skips_without_raising():
    """No signal anywhere (URL/filename/page/metadata) — must skip cleanly
    with a clear reason, never raise or block the rest of the run."""
    with patch(
        "app.services.web_scraper.year_filter.fetch_url_last_modified_date"
    ) as mock_fetch:
        mock_fetch.return_value = None
        year, ok, reason = await evaluate_media_cutoff_async(
            url="https://example.com/fs/resource-manager/view/abc-uuid",
            filename=None,
            source_page_url=None,
        )
    assert year is None
    assert ok is False
    assert reason == "date could not be determined"


@patch(
    "app.services.web_scraper.year_filter.settings.SCHOOL_SCRAPER_ALLOWED_YEARS", ALLOWED
)
@patch("app.services.web_scraper.year_filter.settings.SCHOOL_SCRAPER_MEDIA_CUTOFF_DATE", CUTOFF)
async def test_processability_routes_av_to_cutoff_and_documents_to_year_list():
    av_year, av_ok, _ = await evaluate_media_processability_async(
        media_type="video",
        url="https://example.com/files/20260301_meeting.mp4",  # before cutoff
        filename=None,
        source_page_url=None,
    )
    assert av_year == 2026
    assert av_ok is False  # AV: rejected by the day-precision cutoff

    doc_year, doc_ok, _ = await evaluate_media_processability_async(
        media_type="document",
        url="https://example.com/files/2026-agenda.pdf",  # same year, but a document
        filename=None,
        source_page_url=None,
    )
    assert doc_year == 2026
    assert doc_ok is True  # documents: unaffected, still governed by the year list


# ---------------------------------------------------------------------------
# Keyword gating for audio/video/youtube (documents are not keyword-filtered)
# ---------------------------------------------------------------------------


@patch("app.services.web_scraper.year_filter.settings.SCHOOL_SCRAPER_MEDIA_CUTOFF_DATE", None)
def test_filter_media_files_drops_av_without_matching_keyword():
    media = [
        {
            "url": "https://example.com/files/back-to-school-2026.mp4",
            "name": "Back to School 2026",
            "media_type": "video",
            "source_page_url": "https://example.com/news/",
        },
        {
            "url": "https://example.com/files/2026-03-committee.mp4",
            "name": "School Committee Meeting",
            "media_type": "video",
            "source_page_url": "https://example.com/school-committee/",
        },
    ]
    kept = filter_media_files(media)
    assert len(kept) == 1
    assert kept[0]["name"] == "School Committee Meeting"


@patch("app.services.web_scraper.year_filter.settings.SCHOOL_SCRAPER_MEDIA_CUTOFF_DATE", None)
@patch(
    "app.services.web_scraper.year_filter.settings.SCHOOL_SCRAPER_ALLOWED_YEARS", ALLOWED
)
def test_filter_media_files_does_not_keyword_filter_documents():
    media = [
        {
            "url": "https://example.com/files/2026-random.pdf",
            "name": "Random Newsletter",
            "media_type": "document",
            "source_page_url": "https://example.com/news/",
        },
    ]
    kept = filter_media_files(media)
    assert len(kept) == 1


@patch("app.services.web_scraper.year_filter.settings.SCHOOL_SCRAPER_MEDIA_CUTOFF_DATE", None)
async def test_filter_media_files_async_drops_av_without_matching_keyword():
    media = [
        {
            "url": "https://example.com/files/promo.mp4",
            "name": "Promo Video",
            "media_type": "video",
            "source_page_url": "https://example.com/news/",
        },
        {
            "url": "https://example.com/files/minutes.mp4",
            "name": "Board Meeting Minutes",
            "media_type": "video",
            "source_page_url": "https://example.com/news/",
        },
    ]
    kept = await filter_media_files_async(media)
    assert len(kept) == 1
    assert kept[0]["name"] == "Board Meeting Minutes"
