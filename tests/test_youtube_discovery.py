"""Tests for embedded-YouTube discovery in the school scraper.

Before this, the extractor read only a[href] / source[src] / video[src] /
audio[src] and early-returned unless a known file extension matched, so a
board meeting embedded as a YouTube iframe was completely invisible.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from app.core.config import settings
from app.services.web_scraper.school_scraper_service import SchoolScraperService

VIDEO_ID = "n_SOB-VqQh0"
CANONICAL = f"https://www.youtube.com/watch?v={VIDEO_ID}"
PAGE_URL = "https://example-district.org/board/meetings"


@pytest.fixture(autouse=True)
def _no_real_oembed_calls(monkeypatch):
    """These are unit tests — never hit YouTube's oEmbed endpoint for real."""
    monkeypatch.setattr(
        "app.services.transcription.youtube.fetch_youtube_title",
        AsyncMock(return_value=None),
    )


async def _extract(html: str):
    service = SchoolScraperService()
    media_files, _sub_pages = await service._extract_media_from_page(
        html,
        PAGE_URL,
        settings.SCHOOL_SCRAPER_VIDEO_EXTENSIONS,
        settings.SCHOOL_SCRAPER_AUDIO_EXTENSIONS,
        settings.SCHOOL_SCRAPER_DOCUMENT_EXTENSIONS,
    )
    return media_files


async def test_iframe_youtube_is_discovered():
    html = f"""
    <html><body>
      <h1>Board Meeting</h1>
      <iframe src="https://www.youtube.com/embed/{VIDEO_ID}"
              title="October Board Meeting"></iframe>
    </body></html>
    """
    media = await _extract(html)
    youtube = [m for m in media if m["media_type"] == "youtube"]

    assert len(youtube) == 1
    assert youtube[0]["url"] == CANONICAL
    # No extension exists for a YouTube video — consumers must handle null.
    assert youtube[0]["file_extension"] is None
    assert youtube[0]["source_page_url"] == PAGE_URL


async def test_iframe_title_becomes_the_name():
    html = (
        f'<iframe src="https://www.youtube.com/embed/{VIDEO_ID}" '
        f'title="October Board Meeting"></iframe>'
    )
    youtube = [m for m in await _extract(html) if m["media_type"] == "youtube"]
    assert youtube[0]["name"] == "October Board Meeting"


async def test_anchor_youtube_is_discovered():
    html = f'<a href="https://youtu.be/{VIDEO_ID}">Watch the meeting</a>'
    youtube = [m for m in await _extract(html) if m["media_type"] == "youtube"]
    assert len(youtube) == 1
    assert youtube[0]["url"] == CANONICAL


async def test_same_video_as_iframe_and_anchor_dedups_to_one():
    """A page that both embeds and links the video must not pay twice."""
    html = f"""
    <html><body>
      <iframe src="https://www.youtube.com/embed/{VIDEO_ID}"></iframe>
      <a href="https://www.youtube.com/watch?v={VIDEO_ID}&t=90">Jump to vote</a>
      <a href="https://youtu.be/{VIDEO_ID}">Direct link</a>
    </body></html>
    """
    youtube = [m for m in await _extract(html) if m["media_type"] == "youtube"]
    assert len(youtube) == 1
    assert youtube[0]["url"] == CANONICAL


async def test_lazy_loaded_youtube_in_data_src_is_discovered():
    """Many CMS themes only promote data-src to src client-side."""
    html = (
        f'<div class="video" data-src="https://www.youtube.com/embed/{VIDEO_ID}">'
        "</div>"
    )
    youtube = [m for m in await _extract(html) if m["media_type"] == "youtube"]
    assert len(youtube) == 1
    assert youtube[0]["url"] == CANONICAL


async def test_youtube_inside_a_script_payload_is_discovered():
    html = f"""
    <script>
      window.__DATA__ = {{"video": "https://www.youtube.com/watch?v={VIDEO_ID}"}};
    </script>
    """
    youtube = [m for m in await _extract(html) if m["media_type"] == "youtube"]
    assert len(youtube) == 1


async def test_missing_name_falls_back_to_fetched_youtube_title(monkeypatch):
    """When page context supplies no name, the real video title is fetched."""
    monkeypatch.setattr(
        "app.services.transcription.youtube.fetch_youtube_title",
        AsyncMock(return_value="October Board Meeting"),
    )
    html = (
        f'<div class="video" data-src="https://www.youtube.com/embed/{VIDEO_ID}">'
        "</div>"
    )
    youtube = [m for m in await _extract(html) if m["media_type"] == "youtube"]
    assert youtube[0]["name"] == "October Board Meeting"


async def test_existing_pdf_discovery_is_unchanged():
    """The YouTube path must not disturb the document path."""
    html = """
    <html><body>
      <a href="/files/minutes-2025-10.pdf">October Minutes</a>
      <a href="https://example-district.org/files/agenda.docx">Agenda</a>
    </body></html>
    """
    media = await _extract(html)
    docs = [m for m in media if m["media_type"] == "document"]

    assert len(docs) == 2
    extensions = {m["file_extension"] for m in docs}
    assert extensions == {".pdf", ".docx"}


async def test_mixed_page_reports_every_type():
    html = f"""
    <html><body>
      <a href="/files/minutes.pdf">Minutes</a>
      <a href="/media/meeting.mp3">Audio</a>
      <a href="/media/meeting.mp4">Video</a>
      <iframe src="https://www.youtube.com/embed/{VIDEO_ID}"></iframe>
    </body></html>
    """
    counts: dict[str, int] = {}
    for m in await _extract(html):
        counts[m["media_type"]] = counts.get(m["media_type"], 0) + 1

    assert counts["document"] == 1
    assert counts["audio"] == 1
    assert counts["video"] == 1
    assert counts["youtube"] == 1


async def test_non_video_youtube_urls_are_not_media():
    """A channel or playlist page is not something we can transcribe."""
    html = """
    <a href="https://www.youtube.com/channel/UCabcdefghijklmno">Our channel</a>
    <a href="https://www.youtube.com/@somedistrict">Handle</a>
    """
    youtube = [m for m in await _extract(html) if m["media_type"] == "youtube"]
    assert youtube == []


async def test_vimeo_is_not_treated_as_youtube():
    html = '<iframe src="https://player.vimeo.com/video/123456789"></iframe>'
    youtube = [m for m in await _extract(html) if m["media_type"] == "youtube"]
    assert youtube == []
