"""YouTube captions — the single biggest cost control in this design.

A YouTube video with captions skips collecting, cleaning and transcribing
entirely: the text already exists and costs nothing. Most school board
uploads have captions, so this path carries most of the corpus for $0.

``youtube-transcript-api`` is used rather than ``yt-dlp`` for captions. The
difference is not cosmetic — verified against a real board-meeting video
(``n_SOB-VqQh0``): yt-dlp's default client reported "no captions available"
for a video that has 900 of them, while this library returned all 900. That
failure mode is silent and dangerous: it is indistinguishable from a video
genuinely lacking captions, so it would route the entire corpus to *paid*
transcription with normal-looking logs and a bill an order of magnitude too
large.

When this free path fails — captions genuinely absent, or the request
itself gets blocked — the fallback is Supadata (``supadata.py``), which
fetches the transcript server-side on its own infrastructure rather than us
downloading anything. There is no third attempt: if Supadata also has
nothing, the item is terminal. This replaces an earlier yt-dlp-download +
AssemblyAI fallback that needed a PO Token, a JS runtime and a residential
proxy to work around YouTube's bot-detection, and still hit IP-reputation
blocks in production — see this branch's git history prior to this change.

Hence the rule enforced here: **every fall-through to Supadata logs a
WARNING** that distinguishes "captions genuinely absent" from "captions
unreachable".

Speaker labels: YouTube captions carry none, so segments from this path have
``speaker=None``. That is accepted — timestamps are exact to the millisecond
so click-to-jump works fully; only "who said it" is missing. No LLM inference
and no fallback to paid transcription just to recover speakers.
"""

from __future__ import annotations

import asyncio
import logging
import re
import threading
from urllib.parse import parse_qs, urlparse

from app.core.config import settings
from app.services.transcription.exceptions import (
    MediaUnavailableError,
    TranscriptionProviderError,
)
from app.services.transcription.schemas import (
    CAPTION_KIND_AUTO,
    CAPTION_KIND_MANUAL,
    SOURCE_YOUTUBE_CAPTIONS,
    TranscriptResult,
    TranscriptSegment,
)

logger = logging.getLogger(__name__)

_VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")

# Path-style forms: /embed/<id>, /v/<id>, /live/<id>, /shorts/<id>
_PATH_PREFIXES = ("/embed/", "/v/", "/live/", "/shorts/")

_YOUTUBE_HOSTS = (
    "youtube.com",
    "www.youtube.com",
    "m.youtube.com",
    "music.youtube.com",
    "youtube-nocookie.com",
    "www.youtube-nocookie.com",
)


def extract_youtube_id(url: str) -> str | None:
    """Return the 11-character video ID, or None if ``url`` is not a video.

    Handles watch?v=, youtu.be/, /embed/, /v/, /live/, /shorts/ and the
    nocookie domain. Shared with the page extractor so discovery and
    transcription can never disagree about what counts as the same video.
    """
    if not url:
        return None

    try:
        parsed = urlparse(url.strip())
    except ValueError:
        return None

    host = (parsed.hostname or "").lower()
    path = parsed.path or ""

    if host in ("youtu.be", "www.youtu.be"):
        candidate = path.lstrip("/").split("/")[0]
        return candidate if _VIDEO_ID_RE.match(candidate) else None

    if host not in _YOUTUBE_HOSTS:
        return None

    if path == "/watch":
        values = parse_qs(parsed.query).get("v") or []
        candidate = values[0] if values else ""
        return candidate if _VIDEO_ID_RE.match(candidate) else None

    for prefix in _PATH_PREFIXES:
        if path.startswith(prefix):
            candidate = path[len(prefix) :].split("/")[0]
            return candidate if _VIDEO_ID_RE.match(candidate) else None

    return None


def canonical_youtube_url(url: str) -> str | None:
    """Collapse every URL variant of one video to a single canonical form.

    ``youtu.be/X``, ``/embed/X`` and ``watch?v=X&t=90&list=...`` all become
    ``https://www.youtube.com/watch?v=X`` — which is what makes ``url_hash``
    dedup actually work across pages that embed the same meeting differently.
    """
    video_id = extract_youtube_id(url)
    if not video_id:
        return None
    return f"https://www.youtube.com/watch?v={video_id}"


def is_youtube_url(url: str) -> bool:
    return extract_youtube_id(url) is not None


def extract_playlist_id(url: str) -> str | None:
    """Return the playlist id from a YouTube URL, if present."""
    if not url:
        return None
    try:
        parsed = urlparse(url.strip())
    except ValueError:
        return None
    host = (parsed.hostname or "").lower()
    if host not in _YOUTUBE_HOSTS:
        return None
    values = parse_qs(parsed.query).get("list") or []
    return values[0] if values else None


def is_youtube_scrape_url(url: str) -> bool:
    """True when ``url`` is a direct YouTube video, playlist, or channel entry point."""
    if extract_youtube_id(url) or extract_playlist_id(url):
        return True
    try:
        parsed = urlparse(url.strip())
    except ValueError:
        return False
    host = (parsed.hostname or "").lower()
    if host not in _YOUTUBE_HOSTS:
        return False
    path = parsed.path or ""
    return (
        path.startswith("/playlist")
        or "/channel/" in path
        or path.startswith("/@")
        or path.startswith("/c/")
        or path.startswith("/user/")
    )


class _YouTubeCaptionBudget:
    """Process-local counter for free caption API calls (per Celery worker)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._attempts = 0
        self._exhausted = False

    def reset(self) -> None:
        with self._lock:
            self._attempts = 0
            self._exhausted = False

    def is_exhausted(self) -> bool:
        with self._lock:
            limit = int(getattr(settings, "SCHOOL_SCRAPER_YOUTUBE_CAPTION_BUDGET", 10))
            if limit <= 0:
                return True
            return self._exhausted or self._attempts >= limit

    def record_attempt(self) -> None:
        with self._lock:
            limit = int(getattr(settings, "SCHOOL_SCRAPER_YOUTUBE_CAPTION_BUDGET", 10))
            self._attempts += 1
            if limit > 0 and self._attempts >= limit:
                self._exhausted = True

    def exhaust(self) -> None:
        with self._lock:
            self._exhausted = True


_caption_budget = _YouTubeCaptionBudget()


def reset_youtube_caption_budget() -> None:
    """Test helper — reset the per-process caption budget."""
    _caption_budget.reset()


def youtube_caption_budget_exhausted() -> bool:
    return _caption_budget.is_exhausted()


async def list_youtube_video_urls(url: str) -> list[str]:
    """Expand a YouTube video, playlist, or channel URL to canonical watch URLs."""
    if not settings.SCHOOL_SCRAPER_YOUTUBE_TRANSCRIPT_ENABLED:
        return []

    if not is_youtube_scrape_url(url):
        return []

    if extract_youtube_id(url) and not extract_playlist_id(url):
        canonical = canonical_youtube_url(url)
        return [canonical] if canonical else []

    return await asyncio.to_thread(_list_youtube_videos_sync, url)


def _list_youtube_videos_sync(url: str) -> list[str]:
    """Blocking playlist/channel expansion via yt-dlp (no downloads)."""
    try:
        from yt_dlp import YoutubeDL  # type: ignore[import-untyped]
    except ImportError:  # pragma: no cover
        logger.warning("yt-dlp not installed; cannot expand YouTube playlist %s", url)
        canonical = canonical_youtube_url(url)
        return [canonical] if canonical else []

    opts = _ytdlp_options()
    opts.update(
        {
            "extract_flat": "in_playlist",
            "skip_download": True,
            "ignoreerrors": True,
            "quiet": True,
        }
    )

    try:
        with YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not expand YouTube URL %s: %s", url, exc)
        canonical = canonical_youtube_url(url)
        return [canonical] if canonical else []

    if not info:
        return []

    entries = info.get("entries")
    if not entries:
        vid = info.get("id")
        if isinstance(vid, str) and _VIDEO_ID_RE.match(vid):
            return [f"https://www.youtube.com/watch?v={vid}"]
        canonical = canonical_youtube_url(url)
        return [canonical] if canonical else []

    urls: list[str] = []
    seen: set[str] = set()
    for entry in entries:
        if not entry:
            continue
        candidate: str | None = None
        entry_id = entry.get("id")
        if isinstance(entry_id, str) and _VIDEO_ID_RE.match(entry_id):
            candidate = f"https://www.youtube.com/watch?v={entry_id}"
        else:
            entry_url = entry.get("url") or entry.get("webpage_url")
            if isinstance(entry_url, str):
                candidate = canonical_youtube_url(entry_url)
        if candidate and candidate not in seen:
            seen.add(candidate)
            urls.append(candidate)
    return urls


async def resolve_youtube_media_items(
    url: str,
    *,
    source_page_url: str | None = None,
) -> list[dict] | None:
    """Build scraper media dicts for a direct YouTube fixed URL.

    Returns ``None`` when ``url`` is not a YouTube scrape entry point.
    """
    video_urls = await list_youtube_video_urls(url)
    if not video_urls:
        return None

    page = source_page_url or url
    seen: set[str] = set()
    items: list[dict] = []
    for video_url in video_urls:
        canonical = canonical_youtube_url(video_url)
        if not canonical or canonical in seen:
            continue
        seen.add(canonical)
        name = await fetch_youtube_title(canonical) or canonical
        items.append(
            {
                "name": name,
                "url": canonical,
                "file_extension": None,
                "media_type": "youtube",
                "size_bytes": None,
                "source_page_url": page,
            }
        )
    return items or None


async def fetch_youtube_title(url: str) -> str | None:
    """The video's real title, via YouTube's oEmbed endpoint — no API key.

    A bare video URL never carries a title, so without this every YouTube
    item's display name (and therefore its searchability in the Knowledge
    Base) defaults to the raw URL — unlike a PDF, whose filename already is
    a real title. Called only when page-context anchor text didn't already
    supply a name; oEmbed is a single lightweight JSON GET, not a full page
    fetch.
    """
    import httpx

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(
                "https://www.youtube.com/oembed",
                params={"url": url, "format": "json"},
            )
        if response.status_code != 200:
            return None
        title = response.json().get("title")
        return title.strip() if isinstance(title, str) and title.strip() else None
    except Exception as exc:  # noqa: BLE001 — advisory only, never fatal
        logger.warning("Could not fetch YouTube title for %s: %s", url, exc)
        return None


async def fetch_youtube_transcript(url: str) -> TranscriptResult | None:
    """Fetch captions for ``url``.

    Returns ``None`` when:
    - the video genuinely has no captions (caller may use AssemblyAI), or
    - the per-process caption budget is exhausted, or
    - YouTube rate-limited us (budget is exhausted and caller should use AssemblyAI).

    Raises ``MediaUnavailableError`` (terminal) when the video itself is gone
    or restricted. Other unexpected errors raise ``TranscriptionProviderError``.
    """
    video_id = extract_youtube_id(url)
    if not video_id:
        return None

    if _caption_budget.is_exhausted():
        logger.warning(
            "YouTube caption budget exhausted; skipping free captions for %s — "
            "caller should use AssemblyAI",
            video_id,
        )
        return None

    _caption_budget.record_attempt()
    return await asyncio.to_thread(_fetch_sync, video_id, url)


def _fetch_sync(video_id: str, url: str) -> TranscriptResult | None:
    """Blocking caption fetch. Runs in a worker thread."""
    try:
        from youtube_transcript_api import YouTubeTranscriptApi
        from youtube_transcript_api._errors import (
            NoTranscriptFound,
            TranscriptsDisabled,
            VideoUnavailable,
        )
    except ImportError as exc:  # pragma: no cover
        raise TranscriptionProviderError(
            "youtube-transcript-api is not installed; cannot fetch captions"
        ) from exc

    langs = settings.SCHOOL_SCRAPER_YOUTUBE_SUBTITLE_LANGS

    try:
        api = _build_api(YouTubeTranscriptApi)
        transcript_list = api.list(video_id)

        caption_kind = CAPTION_KIND_MANUAL
        try:
            transcript = transcript_list.find_manually_created_transcript(langs)
        except NoTranscriptFound:
            # Auto-generated captions are still free and still exact on timing.
            transcript = transcript_list.find_generated_transcript(langs)
            caption_kind = CAPTION_KIND_AUTO

        rows = transcript.fetch().to_raw_data()

    except (NoTranscriptFound, TranscriptsDisabled):
        # Captions are GENUINELY absent. This is the only case that may fall
        # through to Supadata.
        logger.warning(
            "No captions available for YouTube video %s (%s) — "
            "falling through to Supadata. Captions genuinely absent.",
            video_id,
            url,
        )
        return None

    except VideoUnavailable as exc:
        raise MediaUnavailableError(
            f"YouTube video {video_id} is unavailable: {exc}"
        ) from exc

    except Exception as exc:
        name = type(exc).__name__
        # IpBlocked / TooManyRequests / RequestBlocked: captions are
        # UNREACHABLE, not absent. Must not be mistaken for "no captions" —
        # raise so Celery retries instead of silently paying.
        if name in ("IpBlocked", "RequestBlocked", "TooManyRequests", "YouTubeRequestFailed"):
            _caption_budget.exhaust()
            logger.warning(
                "YouTube BLOCKED the caption request for %s (%s). Caption budget "
                "exhausted — falling through to Supadata. Set "
                "SCHOOL_SCRAPER_YOUTUBE_PROXY_URL to extend free caption usage.",
                video_id,
                name,
            )
            return None
        if name in ("AgeRestricted", "VideoUnplayable", "InvalidVideoId"):
            raise MediaUnavailableError(
                f"YouTube video {video_id} not retrievable: {name}"
            ) from exc
        raise TranscriptionProviderError(
            f"Unexpected error fetching captions for {video_id}: {name}: {exc}"
        ) from exc

    segments = [
        TranscriptSegment(
            # round(), not int() — int((4.22 + 1.18) * 1000) truncates to
            # 5399 instead of 5400 and the drift accumulates down the file.
            start_ms=round(float(row["start"]) * 1000),
            end_ms=round((float(row["start"]) + float(row.get("duration") or 0)) * 1000),
            text=str(row.get("text") or "").strip(),
            speaker=None,
        )
        for row in rows
    ]
    segments = _clamp_overlaps([s for s in segments if s.text])

    if not segments:
        logger.warning(
            "YouTube video %s returned an empty caption track — falling "
            "through to Supadata.",
            video_id,
        )
        return None

    duration_seconds = round(segments[-1].end_ms / 1000) if segments else None

    logger.info(
        "Fetched %s %s caption segments for YouTube video %s at zero cost",
        len(segments),
        caption_kind,
        video_id,
    )

    return TranscriptResult(
        source=SOURCE_YOUTUBE_CAPTIONS,
        text=" ".join(s.text for s in segments),
        segments=segments,
        language=getattr(transcript, "language_code", None),
        duration_seconds=duration_seconds,
        speech_model=None,
        caption_kind=caption_kind,
    )


def _clamp_overlaps(segments: list[TranscriptSegment]) -> list[TranscriptSegment]:
    """Remove the time overlap between consecutive auto-caption segments.

    YouTube auto-captions are emitted as a ROLLING WINDOW: each row's
    ``start + duration`` typically runs past the next row's ``start``, so raw
    rows overlap by a second or more. Verified on a real board meeting —
    segment 0 ended at 6319 ms while segment 1 started at 4560 ms.

    Left uncorrected, the overlap propagates into chunk boundaries and two
    chunks end up claiming the same seconds of audio, which makes a citation's
    time range ambiguous. Clamping each segment's end to the next segment's
    start yields a strictly non-overlapping timeline while leaving every
    ``start_ms`` — the value a click-to-jump link actually uses — untouched.

    Manually-created captions do not overlap, so this is a no-op for them.
    """
    for current, following in zip(segments, segments[1:], strict=False):
        if current.end_ms > following.start_ms:
            # Never invert a segment; a zero-length one is still a valid anchor.
            current.end_ms = max(current.start_ms, following.start_ms)
    return segments


def _build_api(api_cls):
    """Instantiate the API, wiring a proxy only when one is configured."""
    proxy_url = settings.SCHOOL_SCRAPER_YOUTUBE_PROXY_URL
    if not proxy_url:
        return api_cls()

    try:
        from youtube_transcript_api.proxies import GenericProxyConfig

        return api_cls(
            proxy_config=GenericProxyConfig(
                http_url=proxy_url,
                https_url=proxy_url,
            )
        )
    except ImportError:  # pragma: no cover
        logger.warning(
            "SCHOOL_SCRAPER_YOUTUBE_PROXY_URL is set but this "
            "youtube-transcript-api build has no proxy support; ignoring"
        )
        return api_cls()


def _ytdlp_options() -> dict[str, object]:
    """Shared yt-dlp options, for the metadata/expansion calls only — no
    audio download happens through this module anymore (see supadata.py).
    Cookies defeat "confirm you're not a bot" for those metadata calls.
    """
    opts: dict[str, object] = {
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "socket_timeout": settings.SCHOOL_SCRAPER_YTDLP_TIMEOUT_SECONDS,
    }
    if settings.SCHOOL_SCRAPER_YTDLP_COOKIES_FILE:
        opts["cookiefile"] = settings.SCHOOL_SCRAPER_YTDLP_COOKIES_FILE
    return opts


async def fetch_youtube_upload_year(url: str) -> int | None:
    """Video upload year from YouTube's metadata — no bytes transferred.

    Last-resort fallback for the download-time year filter. A YouTube URL
    never carries a year itself (``youtube.com/watch?v=...``), so on a page
    that isn't year-organized and has no dateable link text nearby, the
    filter would otherwise skip every video regardless of its real upload
    date. Only worth calling once URL/filename/page-context inference has
    already failed, since it costs a metadata round-trip per video.

    Returns None if the metadata cannot be read.
    """
    if not settings.SCHOOL_SCRAPER_YOUTUBE_TRANSCRIPT_ENABLED:
        return None

    try:
        from yt_dlp import YoutubeDL  # type: ignore[import-untyped]
    except ImportError:  # pragma: no cover
        return None

    def _extract() -> int | None:
        with YoutubeDL(_ytdlp_options()) as ydl:
            info = ydl.extract_info(url, download=False) or {}
        upload_date = info.get("upload_date")  # "YYYYMMDD" string, or None
        if isinstance(upload_date, str) and upload_date[:4].isdigit():
            return int(upload_date[:4])
        return None

    try:
        return await asyncio.wait_for(
            asyncio.to_thread(_extract),
            timeout=settings.SCHOOL_SCRAPER_YTDLP_TIMEOUT_SECONDS,
        )
    except Exception as exc:  # noqa: BLE001 — advisory only, never fatal
        logger.warning("Could not read YouTube upload date for %s: %s", url, exc)
        return None
