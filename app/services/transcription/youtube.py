"""YouTube captions — the single biggest cost control in this design.

A YouTube video with captions skips collecting, cleaning and transcribing
entirely: the text already exists and costs nothing. Most school board
uploads have captions, so this path carries most of the corpus for $0.

``youtube-transcript-api`` is used rather than ``yt-dlp`` for captions. The
difference is not cosmetic — verified against a real board-meeting video
(``n_SOB-VqQh0``): yt-dlp's default client is gated behind YouTube's PO Token
and reported "no captions available" for a video that has 900 of them, while
this library returned all 900. That failure mode is silent and dangerous: it
is indistinguishable from a video genuinely lacking captions, so it would
route the entire corpus to *paid* transcription with normal-looking logs and
a bill an order of magnitude too large.

Hence the rule enforced here: **every fall-through to paid transcription logs
a WARNING** that distinguishes "captions genuinely absent" from "captions
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
from pathlib import Path
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

    Returns ``None`` when the video genuinely has no captions — that is the
    signal for the caller to fall back to paid transcription.

    Raises ``MediaUnavailableError`` (terminal) when the video itself is gone
    or restricted, and ``TranscriptionProviderError`` (transient, retried) when
    YouTube rate-limits us, because the latter must never be mistaken for
    "no captions".
    """
    video_id = extract_youtube_id(url)
    if not video_id:
        return None

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
        # through to paid transcription.
        logger.warning(
            "No captions available for YouTube video %s (%s) — "
            "falling through to PAID transcription. Captions genuinely absent.",
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
            logger.error(
                "YouTube BLOCKED the caption request for %s (%s). Captions are "
                "UNREACHABLE, not absent — NOT falling through to paid "
                "transcription. Set SCHOOL_SCRAPER_YOUTUBE_PROXY_URL if this "
                "persists.",
                video_id,
                name,
            )
            raise TranscriptionProviderError(
                f"YouTube blocked caption retrieval for {video_id}: {name}"
            ) from exc
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
            "through to PAID transcription.",
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
    """Shared yt-dlp options. Cookies defeat "confirm you're not a bot"."""
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


async def probe_youtube_duration(url: str) -> int | None:
    """Duration in seconds from YouTube's metadata — no bytes transferred.

    Used to length-gate a caption-less video BEFORE downloading it. Without
    this, an 8-hour livestream is fully downloaded into the shared
    ``temp_uploads`` volume and only then rejected — and that volume is shared
    with the documents worker, so filling it takes down both.

    Returns None if the metadata cannot be read; the caller then falls back to
    probing the downloaded file, which is still capped by size.
    """
    try:
        from yt_dlp import YoutubeDL  # type: ignore[import-untyped]
    except ImportError:  # pragma: no cover
        return None

    def _extract() -> int | None:
        with YoutubeDL(_ytdlp_options()) as ydl:
            info = ydl.extract_info(url, download=False) or {}
        duration = info.get("duration")
        return int(duration) if isinstance(duration, int | float) else None

    try:
        return await asyncio.wait_for(
            asyncio.to_thread(_extract),
            timeout=settings.SCHOOL_SCRAPER_YTDLP_TIMEOUT_SECONDS,
        )
    except Exception as exc:  # noqa: BLE001 — advisory only, never fatal
        logger.warning("Could not read YouTube metadata for %s: %s", url, exc)
        return None


async def download_youtube_audio(url: str, dest_dir: Path) -> Path:
    """Download audio for a caption-less video. Paid path only.

    Returns the downloaded file. Conditioning (if enabled) is handled by
    ``audio_preprocessor`` so there is exactly one audio code path.
    """
    try:
        from yt_dlp import YoutubeDL  # type: ignore[import-untyped]
    except ImportError as exc:  # pragma: no cover
        raise TranscriptionProviderError(
            "yt-dlp is not installed; cannot download YouTube audio"
        ) from exc

    video_id = extract_youtube_id(url) or "video"
    dest_dir.mkdir(parents=True, exist_ok=True)
    outtmpl = str(dest_dir / f"{video_id}.%(ext)s")

    opts = _ytdlp_options()
    opts.update(
        {
            "format": "bestaudio/best",
            "outtmpl": outtmpl,
            # Hard size cap so a mis-detected livestream cannot fill the
            # shared temp_uploads volume even if the length gate was skipped.
            "max_filesize": settings.SCHOOL_SCRAPER_MEDIA_MAX_DOWNLOAD_MB
            * 1024
            * 1024,
        }
    )

    def _download() -> Path:
        with YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
            return Path(ydl.prepare_filename(info))

    try:
        return await asyncio.wait_for(
            asyncio.to_thread(_download),
            timeout=settings.SCHOOL_SCRAPER_YTDLP_TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError as exc:
        raise TranscriptionProviderError(
            f"yt-dlp timed out downloading {video_id}"
        ) from exc
    except Exception as exc:
        raise TranscriptionProviderError(
            f"yt-dlp failed to download {video_id}: {exc}"
        ) from exc
