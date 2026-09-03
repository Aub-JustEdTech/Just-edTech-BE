"""Official YouTube Data API v3 — video-year lookup and playlist/channel
expansion.

Replaces yt-dlp for these two metadata-only jobs. yt-dlp scrapes YouTube's
own pages by impersonating a browser, which is exactly what triggers
YouTube's bot-detection at batch volume (see git history: the 2026-08-18
incident where these same calls stalled whole scrape runs). This API is a
sanctioned, API-keyed request Google explicitly serves for read-only public
data — it cannot be bot-blocked the way scraping can.

Only an API key is needed, no OAuth: OAuth is required only for actions tied
to a specific user's account (managing someone's private playlists,
uploading video, etc). Reading a public video's metadata or a public
playlist's contents needs nothing more than a key. Set YOUTUBE_DATA_API_KEY
(console.cloud.google.com -> enable "YouTube Data API v3" -> create an API
key). videos.list and playlistItems.list each cost 1 unit against the free
10,000-units/day quota.

This module does NOT fetch transcripts/captions — captions.download requires
OAuth from the video's owner (not us, for someone else's channel), so that
job stays on youtube-transcript-api / Supadata (see youtube.py, supadata.py)
unchanged.

Every call here is advisory: a missing key, quota exhaustion, or transport
failure logs a warning and returns None/[] rather than raising, matching the
existing behaviour of the yt-dlp calls this replaces.
"""

from __future__ import annotations

import logging
from urllib.parse import urlparse

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

_REQUEST_TIMEOUT_SECONDS = 30.0
# 20 pages x 50 items/page = 1000 videos. A school board channel/playlist is
# never remotely this large; this is a safety cap against a pathological
# nextPageToken loop, not a real limit anyone should hit.
_MAX_PLAYLIST_PAGES = 20


def _api_key_or_none() -> str | None:
    if not settings.YOUTUBE_DATA_API_KEY:
        logger.warning(
            "YOUTUBE_DATA_API_KEY is not set; skipping YouTube Data API call"
        )
        return None
    return settings.YOUTUBE_DATA_API_KEY


async def fetch_video_upload_year(video_id: str) -> int | None:
    """Upload year for one video, via ``videos.list`` (1 quota unit).

    Returns None if the key is missing, the video isn't found, or the
    request fails for any reason — this is a best-effort lookup, never a
    hard dependency.
    """
    api_key = _api_key_or_none()
    if not api_key:
        return None

    params = {"part": "snippet", "id": video_id, "key": api_key}
    try:
        async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT_SECONDS) as client:
            response = await client.get(
                f"{settings.YOUTUBE_DATA_API_BASE_URL}/videos", params=params
            )
        response.raise_for_status()
        items = response.json().get("items") or []
    except Exception as exc:  # noqa: BLE001 — advisory only, never fatal
        logger.warning(
            "Could not fetch YouTube video metadata for %s: %s", video_id, exc
        )
        return None

    if not items:
        return None

    published_at = items[0].get("snippet", {}).get("publishedAt")
    if (
        isinstance(published_at, str)
        and len(published_at) >= 4
        and published_at[:4].isdigit()
    ):
        return int(published_at[:4])
    return None


async def _list_playlist_video_ids(playlist_id: str, api_key: str) -> list[str]:
    """All video IDs in a playlist, via ``playlistItems.list`` (1 unit/page)."""
    video_ids: list[str] = []
    page_token: str | None = None

    async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT_SECONDS) as client:
        for _ in range(_MAX_PLAYLIST_PAGES):
            params: dict[str, str | int] = {
                "part": "contentDetails",
                "playlistId": playlist_id,
                "maxResults": 50,
                "key": api_key,
            }
            if page_token:
                params["pageToken"] = page_token

            try:
                response = await client.get(
                    f"{settings.YOUTUBE_DATA_API_BASE_URL}/playlistItems",
                    params=params,
                )
                response.raise_for_status()
                payload = response.json()
            except Exception as exc:  # noqa: BLE001 — advisory only, never fatal
                logger.warning(
                    "Could not list playlist %s via YouTube Data API: %s",
                    playlist_id,
                    exc,
                )
                break

            for item in payload.get("items") or []:
                video_id = item.get("contentDetails", {}).get("videoId")
                if isinstance(video_id, str):
                    video_ids.append(video_id)

            page_token = payload.get("nextPageToken")
            if not page_token:
                break

    return video_ids


async def _resolve_channel_uploads_playlist_id(
    channel_ref: dict[str, str], api_key: str
) -> str | None:
    """The channel's "uploads" playlist ID, via ``channels.list`` (1 unit).

    ``channel_ref`` is exactly one of {"id": ...}, {"forHandle": ...} or
    {"forUsername": ...} — whichever the source URL's path form gave us.
    """
    params = {"part": "contentDetails", "key": api_key, **channel_ref}
    try:
        async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT_SECONDS) as client:
            response = await client.get(
                f"{settings.YOUTUBE_DATA_API_BASE_URL}/channels", params=params
            )
        response.raise_for_status()
        items = response.json().get("items") or []
    except Exception as exc:  # noqa: BLE001 — advisory only, never fatal
        logger.warning(
            "Could not resolve YouTube channel %s via Data API: %s",
            channel_ref,
            exc,
        )
        return None

    if not items:
        return None
    return (
        items[0].get("contentDetails", {}).get("relatedPlaylists", {}).get("uploads")
    )


def _channel_ref_from_url(url: str) -> dict[str, str] | None:
    """Map a channel URL's path form to the matching ``channels.list`` param.

    ``/channel/<id>`` carries the channel ID directly; ``/@handle`` is the
    current handle form; ``/c/<name>`` and ``/user/<name>`` are legacy custom
    URLs, best-effort mapped to ``forUsername`` (not guaranteed to resolve
    for every legacy ``/c/`` custom name, since those were never guaranteed
    to equal the underlying username — but this covers the common case).
    """
    parsed = urlparse(url.strip())
    path = (parsed.path or "").strip("/")

    if path.startswith("channel/"):
        channel_id = path.split("/", 2)[1] if "/" in path else ""
        return {"id": channel_id} if channel_id else None
    if path.startswith("@"):
        handle = path.split("/", 1)[0]
        return {"forHandle": handle} if handle else None
    if path.startswith("c/") or path.startswith("user/"):
        name = path.split("/", 1)[1].split("/")[0]
        return {"forUsername": name} if name else None
    return None


async def list_channel_or_playlist_video_ids(
    url: str, *, playlist_id: str | None = None
) -> list[str]:
    """Video IDs for a playlist or channel entry point, via the Data API.

    Pass ``playlist_id`` when the caller already extracted a ``?list=``
    query param; otherwise ``url`` is treated as a channel entry point
    (``/channel/...``, ``/@handle``, ``/c/...``, ``/user/...``).
    """
    api_key = _api_key_or_none()
    if not api_key:
        return []

    if playlist_id:
        return await _list_playlist_video_ids(playlist_id, api_key)

    channel_ref = _channel_ref_from_url(url)
    if not channel_ref:
        logger.warning("Could not determine channel reference from %s", url)
        return []

    uploads_playlist_id = await _resolve_channel_uploads_playlist_id(
        channel_ref, api_key
    )
    if not uploads_playlist_id:
        return []

    return await _list_playlist_video_ids(uploads_playlist_id, api_key)
