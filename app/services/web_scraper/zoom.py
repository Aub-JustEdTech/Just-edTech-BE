"""Zoom cloud-recording share-link resolution and download.

A Zoom recording share link (``.../rec/share/<token>``) is not itself a
fetchable media file — it is an HTML page that, for recordings with no
passcode (or whose passcode is embedded in the URL), redirects to a
``/rec/play/...`` page and loads the actual video from a short-lived, signed
CDN URL (``https://ssrweb.zoom.us/replay0N/.../<name>.mp4?...&Signature=...``).

That signed URL is session-bound, not a plain public link: verified against
a real public share link (Holliston Public Schools, 2026-09) that a cold,
cookie-less request to the resolved URL — same URL, no browser session —
returns ``403``, while a request issued through the SAME browser context
that resolved it succeeds. This is the same shape of problem
``board_platforms.fetch_document_via_playwright_session`` already solves for
board-platform documents (cookies/referrer from a real browser session), so
this module downloads the actual bytes through the resolving browser
context's own request API rather than handing back a URL for some other
process (e.g. AssemblyAI's remote fetch under ``url_direct``) to fetch
independently — that would 403.

Passcode-protected recordings whose passcode is NOT embedded in the shared
URL cannot be resolved — there is no way to learn it from a public scrape —
and are reported back as :class:`ZoomPasscodeRequiredError` rather than
attempted.
"""

from __future__ import annotations

import asyncio
import logging
import re

from app.core.config import settings
from app.services.transcription.exceptions import (
    ZoomPasscodeRequiredError,
    ZoomRecordingUnavailableError,
)

logger = logging.getLogger(__name__)

# The playable recording response is served from a *.zoom.us CDN host as an
# .mp4 (progressive) or .m3u8 (HLS) response — the "replay0N" path prefix and
# the Policy/Signature/Key-Pair-Id query params (a CloudFront-style signed
# URL) are what actually vary between recordings.
_RECORDING_MEDIA_RE = re.compile(r"\.zoom\.us/.*\.(mp4|m3u8)(\?|$)", re.IGNORECASE)

_PASSCODE_SELECTOR = (
    'input[type="password"], input[name="passwd"], input[id*="passcode" i]'
)

_NAV_TIMEOUT_MS = 20_000
_MEDIA_WAIT_TIMEOUT_SECONDS = 15


def _chromium_launch_kwargs() -> dict:
    """Build kwargs for ``chromium.launch()``.

    Duplicated from ``board_platforms._chromium_launch_kwargs`` /
    ``SchoolScraperService._chromium_launch_kwargs`` (same reasoning: avoids
    a circular import) so the Docker system-Chromium path
    (``PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH``) is honoured here too.
    """
    kwargs: dict = {"headless": True}
    executable_path = getattr(settings, "PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH", None)
    if executable_path:
        kwargs["executable_path"] = executable_path
        kwargs["args"] = ["--no-sandbox"]
    return kwargs


async def download_zoom_recording(share_url: str) -> bytes:
    """Download a Zoom recording's actual media bytes.

    Drives a headless browser to the share page, waits for the signed CDN
    media URL to appear in the page's own network traffic, then re-fetches
    that exact URL through the SAME browser context so its session cookies
    are attached — a fresh, cookie-less request to the same URL returns 403.

    Raises :class:`ZoomPasscodeRequiredError` when the share page is
    passcode-gated, or :class:`ZoomRecordingUnavailableError` when the page
    can't be reached, never produces a playable media response, or the
    authenticated re-fetch itself fails. Both are ``TerminalTranscriptionError``
    subclasses — callers should let them propagate to be recorded as a clean
    skip, not retried.
    """
    from playwright.async_api import async_playwright

    found = asyncio.Event()
    media_url: str | None = None

    pw = await async_playwright().start()
    browser = None
    context = None
    try:
        browser = await pw.chromium.launch(**_chromium_launch_kwargs())
        context = await browser.new_context()
        page = await context.new_page()

        def _on_response(response) -> None:
            nonlocal media_url
            if media_url is None and _RECORDING_MEDIA_RE.search(response.url):
                media_url = response.url
                found.set()

        page.on("response", _on_response)

        try:
            await page.goto(
                share_url, timeout=_NAV_TIMEOUT_MS, wait_until="domcontentloaded"
            )
        except Exception as exc:  # noqa: BLE001 — any nav failure is unavailable
            raise ZoomRecordingUnavailableError(
                f"Could not load Zoom share page {share_url}: {exc}"
            ) from exc

        try:
            passcode_input = await page.query_selector(_PASSCODE_SELECTOR)
        except Exception:  # noqa: BLE001 — a selector-check failure isn't a passcode
            passcode_input = None
        if passcode_input is not None:
            raise ZoomPasscodeRequiredError(
                f"Recording at {share_url} is passcode-protected; "
                "passcode is not present in the shared URL"
            )

        try:
            await asyncio.wait_for(
                found.wait(), timeout=_MEDIA_WAIT_TIMEOUT_SECONDS
            )
        except asyncio.TimeoutError:
            pass  # handled by the media_url is None check below

        if media_url is None:
            raise ZoomRecordingUnavailableError(
                f"No playable recording URL was found on the share page {share_url} "
                "(deleted, expired, or the player never loaded)"
            )

        # Re-fetch through the SAME context so its cookies are attached —
        # the resolved URL 403s without them (see module docstring).
        timeout_ms = int(getattr(settings, "WEB_SCRAPER_TIMEOUT_SECONDS", 30) * 1000)
        response = await context.request.get(media_url, timeout=timeout_ms)
        status = response.status
        if not 200 <= status < 300:
            raise ZoomRecordingUnavailableError(
                f"Authenticated download of {media_url} returned HTTP {status}"
            )
        raw = await response.body()
        if not raw:
            raise ZoomRecordingUnavailableError(
                f"Authenticated download of {media_url} returned an empty body"
            )
    finally:
        if context is not None:
            try:
                await context.close()
            except Exception:  # noqa: BLE001
                pass
        if browser is not None:
            try:
                await browser.close()
            except Exception:  # noqa: BLE001
                pass
        try:
            await pw.stop()
        except Exception:  # noqa: BLE001
            pass

    logger.info(
        "zoom: downloaded %d bytes from %s (resolved via %s)",
        len(raw),
        share_url,
        media_url,
    )
    return raw
