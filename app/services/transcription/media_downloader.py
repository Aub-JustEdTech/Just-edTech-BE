"""Media duration probing and (fallback-only) streaming download.

``probe_duration_seconds`` is the cost guard and runs on EVERY paid-path item:
ffprobe reads only the file header, so it works against a REMOTE URL in ~1.5s
with no download. That is what lets the duration cap reject an over-long
livestream *before* any money is spent.

``stream_download_to_disk`` is used only by the ``preprocess`` audio mode and
the YouTube no-captions path. Under the default ``url_direct`` mode nothing
here downloads anything.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path

import httpx

from app.core.config import settings
from app.services.transcription.exceptions import (
    MediaTooLargeError,
    TranscriptionProviderError,
)

logger = logging.getLogger(__name__)

_CHUNK_BYTES = 1024 * 1024  # 1 MiB


@dataclass(slots=True)
class MediaProbe:
    """What a header read can tell us before spending anything.

    ``probed`` distinguishes "we looked and there is no audio" from "we could
    not look at all" — conflating the two either bills for silent files or
    silently skips real meetings.
    """

    probed: bool = False
    duration_seconds: int | None = None
    has_audio: bool = False
    has_video: bool = False
    audio_codec: str | None = None
    # Byte size straight from the container header — the only way to record
    # size_bytes under url_direct, where the file is never downloaded.
    size_bytes: int | None = None


async def probe_media(path_or_url: str) -> MediaProbe:
    """Read duration and stream layout without downloading the file.

    Works on both a local path and a remote URL: for a URL ffprobe issues a
    ranged read of the header only, which returns in ~1.5s regardless of
    whether the file is 10 seconds or 3 hours. That is what makes the cost
    gates free to apply.

    Never raises. A probe that fails returns ``probed=False`` so the caller
    can decide, rather than being handed a misleading ``has_audio=False``.
    """
    args = [
        settings.TRANSCRIPTION_FFPROBE_PATH,
        "-v",
        "error",
        "-show_entries",
        "format=duration,size:stream=codec_type,codec_name",
        "-of",
        "json",
        path_or_url,
    ]

    try:
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError:
        logger.warning(
            "ffprobe not found at %s; media gates cannot be enforced",
            settings.TRANSCRIPTION_FFPROBE_PATH,
        )
        return MediaProbe()

    try:
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(),
            timeout=settings.TRANSCRIPTION_FFPROBE_TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError:
        # A hung remote read must not stall the worker.
        proc.kill()
        await proc.wait()
        logger.warning("ffprobe timed out probing %s", path_or_url)
        return MediaProbe()

    if proc.returncode != 0:
        logger.warning(
            "ffprobe failed for %s (rc=%s): %s",
            path_or_url,
            proc.returncode,
            stderr.decode("utf-8", errors="replace")[-500:],
        )
        return MediaProbe()

    return parse_ffprobe_json(stdout.decode("utf-8", errors="replace"), path_or_url)


def parse_ffprobe_json(raw: str, path_or_url: str = "") -> MediaProbe:
    """Parse ffprobe's JSON output. Pure function, so it is testable."""
    try:
        payload = json.loads(raw or "{}")
    except json.JSONDecodeError:
        logger.warning("ffprobe returned unparseable JSON for %s", path_or_url)
        return MediaProbe()

    streams = payload.get("streams") or []
    audio_codec: str | None = None
    has_audio = False
    has_video = False
    # Single pass: these lists were only ever used for a truthiness test and
    # the first audio codec.
    for stream in streams:
        codec_type = stream.get("codec_type")
        if codec_type == "audio":
            if not has_audio:
                audio_codec = stream.get("codec_name")
            has_audio = True
        elif codec_type == "video":
            has_video = True

    fmt = payload.get("format") or {}
    return MediaProbe(
        probed=True,
        duration_seconds=_coerce_int(fmt.get("duration")),
        has_audio=has_audio,
        has_video=has_video,
        audio_codec=audio_codec,
        size_bytes=_coerce_int(fmt.get("size")),
    )


def _coerce_int(value: object) -> int | None:
    """ffprobe reports numbers as strings; missing keys are absent entirely."""
    if value is None:
        return None
    try:
        return int(float(value))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


async def probe_duration_seconds(path_or_url: str) -> int | None:
    """Duration only, for callers that do not care about stream layout."""
    return (await probe_media(path_or_url)).duration_seconds


async def stream_download_to_disk(
    url: str,
    dest: Path,
    *,
    max_bytes: int | None = None,
    user_agent: str | None = None,
    timeout: float | None = None,
) -> tuple[int, str]:
    """Stream ``url`` to ``dest``, returning ``(size_bytes, sha256_hex)``.

    Hashes incrementally so ``content_hash`` is available without ever
    holding the file in memory. Aborts and deletes the partial file if the
    cap is exceeded mid-stream.
    """
    cap = (
        max_bytes
        if max_bytes is not None
        else settings.SCHOOL_SCRAPER_MEDIA_MAX_DOWNLOAD_MB * 1024 * 1024
    )
    headers = {"User-Agent": user_agent or settings.SCHOOL_SCRAPER_USER_AGENT}
    request_timeout = timeout or settings.WEB_SCRAPER_TIMEOUT_SECONDS

    dest.parent.mkdir(parents=True, exist_ok=True)
    hasher = hashlib.sha256()
    written = 0

    try:
        async with httpx.AsyncClient(
            timeout=request_timeout,
            headers=headers,
            follow_redirects=True,
        ) as client:
            async with client.stream("GET", url) as response:
                response.raise_for_status()

                # Short-circuit on an advertised over-cap size, before any body.
                declared = response.headers.get("Content-Length")
                if declared and declared.isdigit() and int(declared) > cap:
                    raise MediaTooLargeError(
                        f"{url} declares {int(declared)} bytes, cap is {cap}"
                    )

                with dest.open("wb") as fh:
                    async for chunk in response.aiter_bytes(_CHUNK_BYTES):
                        written += len(chunk)
                        if written > cap:
                            raise MediaTooLargeError(
                                f"{url} exceeded {cap} bytes while downloading"
                            )
                        hasher.update(chunk)
                        fh.write(chunk)
    except MediaTooLargeError:
        _unlink_quietly(dest)
        raise
    except httpx.HTTPError as exc:
        _unlink_quietly(dest)
        raise TranscriptionProviderError(f"Download failed for {url}: {exc}") from exc
    except Exception:
        _unlink_quietly(dest)
        raise

    return written, hasher.hexdigest()


def _unlink_quietly(path: Path) -> None:
    try:
        os.unlink(path)
    except OSError:
        pass
