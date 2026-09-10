"""Unit tests for app.services.web_scraper.zoom.

Run:
    poetry run pytest tests/test_zoom.py -v
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.transcription.exceptions import (
    ZoomPasscodeRequiredError,
    ZoomRecordingUnavailableError,
)
from app.services.web_scraper.zoom import _RECORDING_MEDIA_RE, download_zoom_recording

# A real signed CDN URL captured from a live, non-passcode-gated Holliston
# Public Schools Zoom recording share link (2026-09) — see the module
# docstring in zoom.py for how it was verified.
REAL_RECORDING_MEDIA_URL = (
    "https://ssrweb.zoom.us/replay02/2026/03/19/9A917445-92C5-485A-9902-"
    "0E187873261B/GMT20260319-220337_Recording_640x360.mp4"
    "?response-content-type=video%2Fmp4&Policy=abc&Signature=def&Key-Pair-Id=ghi"
)

REAL_SHARE_URL = (
    "https://holliston-k12-ma-us.zoom.us/rec/share/"
    "a21EtS-B6UT1HFLBFy9DYxzGQEtoM6W2VXWi02_cHADAtzkCtwz2dqrNjR5lJbu9"
    ".xCY9NPFspQNEAq7W?from=hub"
)


# ---------------------------------------------------------------------------
# _RECORDING_MEDIA_RE — the "is this response the actual recording?" filter
# ---------------------------------------------------------------------------


def test_recording_media_regex_matches_real_captured_url():
    assert _RECORDING_MEDIA_RE.search(REAL_RECORDING_MEDIA_URL) is not None


def test_recording_media_regex_matches_hls_variant():
    assert (
        _RECORDING_MEDIA_RE.search(
            "https://ssrweb.zoom.us/replay01/2026/01/01/abc/index.m3u8?Signature=x"
        )
        is not None
    )


def test_recording_media_regex_does_not_match_share_page_itself():
    assert _RECORDING_MEDIA_RE.search(REAL_SHARE_URL) is None


def test_recording_media_regex_does_not_match_unrelated_static_asset():
    assert (
        _RECORDING_MEDIA_RE.search("https://st-zoom-us.zoom.us/static/bundle.js")
        is None
    )


# ---------------------------------------------------------------------------
# download_zoom_recording — mocked Playwright chain
# ---------------------------------------------------------------------------


def _make_fake_playwright(
    *, goto_side_effect=None, passcode_input=None, request_response=None
):
    """Build a fake ``async_playwright()`` chain.

    ``goto_side_effect`` receives no args and runs during ``page.goto`` —
    used to simulate the recording-media response arriving mid-navigation,
    exactly as it does in a real browser (the response event fires while
    the page is still loading). ``request_response`` is what
    ``context.request.get(...)`` (the authenticated re-fetch) returns.
    """
    page = MagicMock()
    page.on = MagicMock()
    page.goto = AsyncMock(side_effect=goto_side_effect)
    page.query_selector = AsyncMock(return_value=passcode_input)

    context = MagicMock()
    context.new_page = AsyncMock(return_value=page)
    context.close = AsyncMock()
    context.request.get = AsyncMock(return_value=request_response)

    browser = MagicMock()
    browser.new_context = AsyncMock(return_value=context)
    browser.close = AsyncMock()

    pw_instance = MagicMock()
    pw_instance.chromium.launch = AsyncMock(return_value=browser)
    pw_instance.stop = AsyncMock()

    pw_context = MagicMock()
    pw_context.start = AsyncMock(return_value=pw_instance)

    return pw_context, page, context


def _fake_response(status: int, body: bytes):
    resp = MagicMock()
    resp.status = status
    resp.body = AsyncMock(return_value=body)
    return resp


@pytest.mark.asyncio
async def test_downloads_bytes_on_success():
    on_response_holder: dict = {}

    def _capture_on(event, callback):
        on_response_holder["callback"] = callback

    async def _goto_fires_media_response(*args, **kwargs):
        # Simulate the recording's signed URL arriving as a network
        # response while the page is still loading — exactly what the
        # live Playwright MCP check observed for a real share link.
        fake_response = SimpleNamespace(url=REAL_RECORDING_MEDIA_URL)
        on_response_holder["callback"](fake_response)

    pw_context, page, _context = _make_fake_playwright(
        goto_side_effect=_goto_fires_media_response,
        passcode_input=None,
        request_response=_fake_response(200, b"fake mp4 bytes"),
    )
    page.on = MagicMock(side_effect=_capture_on)

    with patch("playwright.async_api.async_playwright", return_value=pw_context):
        result = await download_zoom_recording(REAL_SHARE_URL)

    assert result == b"fake mp4 bytes"


@pytest.mark.asyncio
async def test_raises_passcode_required_when_passcode_input_present():
    pw_context, _page, _context = _make_fake_playwright(
        goto_side_effect=None, passcode_input=MagicMock()
    )

    with patch("playwright.async_api.async_playwright", return_value=pw_context):
        with pytest.raises(ZoomPasscodeRequiredError):
            await download_zoom_recording(REAL_SHARE_URL)


@pytest.mark.asyncio
async def test_raises_unavailable_when_navigation_fails():
    pw_context, _page, _context = _make_fake_playwright(
        goto_side_effect=RuntimeError("net::ERR_NAME_NOT_RESOLVED")
    )

    with patch("playwright.async_api.async_playwright", return_value=pw_context):
        with pytest.raises(ZoomRecordingUnavailableError):
            await download_zoom_recording(REAL_SHARE_URL)


@pytest.mark.asyncio
async def test_raises_unavailable_when_no_media_response_ever_arrives():
    with patch("app.services.web_scraper.zoom._MEDIA_WAIT_TIMEOUT_SECONDS", 0.05):
        pw_context, _page, _context = _make_fake_playwright(
            goto_side_effect=None, passcode_input=None
        )
        with patch("playwright.async_api.async_playwright", return_value=pw_context):
            with pytest.raises(ZoomRecordingUnavailableError):
                await download_zoom_recording(REAL_SHARE_URL)


@pytest.mark.asyncio
async def test_raises_unavailable_when_authenticated_download_is_blocked():
    """The real-world case: Zoom's CDN 403s even the authenticated re-fetch."""
    on_response_holder: dict = {}

    def _capture_on(event, callback):
        on_response_holder["callback"] = callback

    async def _goto_fires_media_response(*args, **kwargs):
        fake_response = SimpleNamespace(url=REAL_RECORDING_MEDIA_URL)
        on_response_holder["callback"](fake_response)

    pw_context, page, _context = _make_fake_playwright(
        goto_side_effect=_goto_fires_media_response,
        passcode_input=None,
        request_response=_fake_response(403, b""),
    )
    page.on = MagicMock(side_effect=_capture_on)

    with patch("playwright.async_api.async_playwright", return_value=pw_context):
        with pytest.raises(ZoomRecordingUnavailableError):
            await download_zoom_recording(REAL_SHARE_URL)
