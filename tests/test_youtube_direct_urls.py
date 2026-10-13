"""Direct YouTube fixed URLs and caption-budget → AssemblyAI fallback."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.config import settings
from app.services.transcription.schemas import (
    SOURCE_ASSEMBLYAI,
    SOURCE_YOUTUBE_CAPTIONS,
    TranscriptResult,
    TranscriptSegment,
)
from app.services.transcription.service import TranscriptionService
from app.services.transcription.youtube import (
    canonical_youtube_url,
    fetch_youtube_transcript,
    is_youtube_scrape_url,
    list_youtube_video_urls,
    reset_youtube_caption_budget,
    resolve_youtube_media_items,
    youtube_caption_budget_exhausted,
)
from app.services.web_scraper.school_scraper_service import SchoolScraperService

VIDEO_ID = "n_SOB-VqQh0"
VIDEO_URL = f"https://www.youtube.com/watch?v={VIDEO_ID}"
PLAYLIST_URL = "https://www.youtube.com/playlist?list=PLypOllJHc4M2EgOMD_XarFqL3EXJi8KCv"
OTHER_VIDEO = "https://www.youtube.com/watch?v=abc12345678"


@pytest.fixture(autouse=True)
def _reset_caption_budget():
    reset_youtube_caption_budget()
    yield
    reset_youtube_caption_budget()


@pytest.fixture(autouse=True)
def _no_real_oembed(monkeypatch):
    monkeypatch.setattr(
        "app.services.transcription.youtube.fetch_youtube_title",
        AsyncMock(return_value="Board Meeting"),
    )


def test_is_youtube_scrape_url():
    assert is_youtube_scrape_url(VIDEO_URL) is True
    assert is_youtube_scrape_url(PLAYLIST_URL) is True
    assert is_youtube_scrape_url("https://www.youtube.com/channel/UCabc") is True
    assert is_youtube_scrape_url("https://example.org/minutes.pdf") is False


async def test_resolve_single_youtube_video():
    items = await resolve_youtube_media_items(VIDEO_URL)
    assert items is not None
    assert len(items) == 1
    assert items[0]["media_type"] == "youtube"
    assert items[0]["url"] == canonical_youtube_url(VIDEO_URL)
    assert items[0]["source_page_url"] == VIDEO_URL


async def test_resolve_youtube_playlist_expands_videos(monkeypatch):
    async def _fake_list(url: str) -> list[str]:
        assert url == PLAYLIST_URL
        return [VIDEO_URL, OTHER_VIDEO]

    monkeypatch.setattr(
        "app.services.transcription.youtube.list_youtube_video_urls",
        _fake_list,
    )
    items = await resolve_youtube_media_items(PLAYLIST_URL)
    assert items is not None
    assert len(items) == 2
    assert {i["url"] for i in items} == {
        canonical_youtube_url(VIDEO_URL),
        canonical_youtube_url(OTHER_VIDEO),
    }


async def test_scrape_media_files_accepts_direct_youtube_url(monkeypatch):
    async def _fake_resolve(url: str, *, source_page_url: str | None = None):
        return [
            {
                "name": "Meeting",
                "url": VIDEO_URL,
                "file_extension": None,
                "media_type": "youtube",
                "size_bytes": None,
                "source_page_url": source_page_url or url,
            }
        ]

    monkeypatch.setattr(
        "app.services.transcription.youtube.resolve_youtube_media_items",
        _fake_resolve,
    )

    async with SchoolScraperService(use_playwright=False) as svc:
        result = await svc.scrape_media_files(page_url=VIDEO_URL, crawl_depth=0)

    assert result["pages_crawled"] == 1
    assert len(result["media_files"]) == 1
    assert result["media_files"][0]["media_type"] == "youtube"


async def test_caption_budget_skips_api_after_limit(monkeypatch):
    monkeypatch.setattr(settings, "SCHOOL_SCRAPER_YOUTUBE_CAPTION_BUDGET", 2)

    calls: list[str] = []

    def _fake_fetch_sync(video_id: str, url: str):
        calls.append(video_id)
        return TranscriptResult(
            source=SOURCE_YOUTUBE_CAPTIONS,
            text="hello",
            segments=[TranscriptSegment(0, 1000, "hello", None)],
        )

    monkeypatch.setattr(
        "app.services.transcription.youtube._fetch_sync",
        _fake_fetch_sync,
    )

    assert await fetch_youtube_transcript(VIDEO_URL) is not None
    assert await fetch_youtube_transcript(OTHER_VIDEO) is not None
    assert youtube_caption_budget_exhausted() is True
    assert await fetch_youtube_transcript(VIDEO_URL) is None
    assert calls == [VIDEO_ID, "abc12345678"]


async def test_rate_limit_exhausts_budget_and_returns_none(monkeypatch):
    class IpBlocked(Exception):
        pass

    class FakeApi:
        def list(self, video_id: str):
            raise IpBlocked("blocked")

    monkeypatch.setattr(
        "app.services.transcription.youtube._build_api",
        lambda _cls: FakeApi(),
    )

    assert await fetch_youtube_transcript(VIDEO_URL) is None
    assert youtube_caption_budget_exhausted() is True


async def test_transcribe_youtube_uses_assemblyai_when_budget_exhausted(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "SCHOOL_SCRAPER_YOUTUBE_CAPTION_BUDGET", 0)
    monkeypatch.setattr(settings, "SCHOOL_SCRAPER_WHISPER_TRANSCRIPTION_ENABLED", True)
    monkeypatch.setattr(settings, "SCHOOL_SCRAPER_YOUTUBE_AUDIO_FALLBACK_ENABLED", True)

    paid_result = TranscriptResult(
        source=SOURCE_ASSEMBLYAI,
        text="paid transcript",
        segments=[TranscriptSegment(0, 1000, "paid transcript", "A")],
        speech_model="universal-2",
    )

    service = TranscriptionService(client=MagicMock())
    monkeypatch.setattr(
        service,
        "_transcribe_local_file",
        AsyncMock(return_value=paid_result),
    )
    monkeypatch.setattr(
        "app.services.transcription.service.probe_youtube_duration",
        AsyncMock(return_value=120),
    )
    monkeypatch.setattr(
        "app.services.transcription.service.download_youtube_audio",
        AsyncMock(return_value=tmp_path / f"{VIDEO_ID}.m4a"),
    )
    monkeypatch.setattr(
        service,
        "enforce_media_gates",
        AsyncMock(return_value=MagicMock(duration_seconds=120, size_bytes=1024)),
    )

    result = await service.transcribe_youtube(VIDEO_URL, workdir=tmp_path)

    assert result.source == SOURCE_ASSEMBLYAI
    service._transcribe_local_file.assert_awaited_once()


async def test_list_youtube_video_urls_single_video():
    urls = await list_youtube_video_urls(VIDEO_URL)
    assert urls == [VIDEO_URL]


async def test_list_youtube_video_urls_empty_when_transcript_disabled(monkeypatch):
    monkeypatch.setattr(settings, "SCHOOL_SCRAPER_YOUTUBE_TRANSCRIPT_ENABLED", False)
    assert await list_youtube_video_urls(PLAYLIST_URL) == []


async def test_fetch_youtube_upload_year_skips_ytdlp_when_transcript_disabled(
    monkeypatch,
):
    from app.services.transcription.youtube import fetch_youtube_upload_year

    monkeypatch.setattr(settings, "SCHOOL_SCRAPER_YOUTUBE_TRANSCRIPT_ENABLED", False)
    with patch("app.services.transcription.youtube.asyncio.to_thread") as mock_thread:
        result = await fetch_youtube_upload_year(VIDEO_URL)
    mock_thread.assert_not_called()
    assert result is None
