"""Transcription orchestration — the cost gates, in cheapest-first order.

    is it YouTube? -> has captions? -> has audio? -> long enough? -> under cap?
         free            free           free          free           paid

Every gate after the caption check comes from a single ffprobe header read
(~1.5s, no download), so the whole chain is free to apply. Only an item that
passes all of them costs money (~$0.23/audio-hour).
"""

from __future__ import annotations

import logging
from pathlib import Path

from app.core.config import settings
from app.services.transcription.assemblyai_client import AssemblyAIClient
from app.services.transcription.audio_preprocessor import preprocess_to_wav
from app.services.transcription.exceptions import (
    MediaHasNoAudioError,
    MediaTooLongError,
    MediaTooShortError,
    NoTranscriptAvailableError,
)
from app.services.transcription.media_downloader import (
    MediaProbe,
    probe_media,
    stream_download_to_disk,
)
from app.services.transcription.schemas import TranscriptResult
from app.services.transcription.youtube import (
    download_youtube_audio,
    fetch_youtube_transcript,
    probe_youtube_duration,
)

logger = logging.getLogger(__name__)


class TranscriptionService:
    """Routes a media item down the cheapest path that can transcribe it."""

    def __init__(self, client: AssemblyAIClient | None = None):
        self._client = client or AssemblyAIClient()

    async def enforce_media_gates(self, path_or_url: str) -> MediaProbe:
        """Run every pre-spend gate. Returns the probe for the caller to reuse.

        Returning the whole ``MediaProbe`` rather than just the duration means
        ``size_bytes`` is available without a second ffprobe call — which is
        the only way to record it under ``url_direct``, where the file is
        never downloaded.

        Three checks, all from a single ffprobe header read (~1.5s, no
        download), so they are free to apply:

        1. **has an audio stream** — a file with none has nothing to
           transcribe, yet providers bill per audio-hour submitted either way.
        2. **long enough** — catches decorative clips that carry a silent or
           music-only track, which check 1 cannot see.
        3. **under the duration cap** — rejects an over-long livestream.

        Fails OPEN when the probe itself fails: an unreadable header must not
        silently drop a real meeting recording. That is logged, because the
        gates stop protecting that item.
        """
        probe = await probe_media(path_or_url)

        if not probe.probed:
            logger.warning(
                "Could not probe %s; NO cost gate applies to this item and it "
                "will be sent for paid transcription unchecked",
                path_or_url,
            )
            return probe

        # 1. No audio stream at all — nothing to transcribe.
        if settings.transcription_require_audio and not probe.has_audio:
            raise MediaHasNoAudioError(
                f"{path_or_url} has no audio stream "
                f"(video={probe.has_video}, duration={probe.duration_seconds}s)"
            )

        duration = probe.duration_seconds
        if duration is None:
            logger.warning(
                "No duration for %s; length gates cannot be enforced", path_or_url
            )
            return probe

        self._enforce_duration_bounds(path_or_url, duration, probe.audio_codec)
        return probe

    @staticmethod
    def _enforce_duration_bounds(
        label: str, duration: int, audio_codec: str | None = None
    ) -> None:
        """Length gates, shared by the URL and downloaded-file paths."""
        floor = settings.transcription_min_duration_seconds
        if floor and duration < floor:
            raise MediaTooShortError(
                f"{label} is {duration}s, below the {floor}s floor "
                f"(codec={audio_codec})"
            )

        cap_seconds = settings.transcription_max_duration_minutes * 60
        if duration > cap_seconds:
            raise MediaTooLongError(
                f"{label} is {duration}s, cap is {cap_seconds}s "
                f"({settings.transcription_max_duration_minutes} min)"
            )

    async def transcribe_youtube(
        self,
        url: str,
        *,
        workdir: Path | None = None,
    ) -> TranscriptResult:
        """Gate 2 + Gate 3: captions if they exist, otherwise paid audio.

        Captions are tried first when the per-process budget allows. After
        ``SCHOOL_SCRAPER_YOUTUBE_CAPTION_BUDGET`` attempts, or when YouTube
        rate-limits us, caption fetch is skipped and AssemblyAI is used.
        """
        result = await fetch_youtube_transcript(url)
        if result is not None:
            return result

        # No captions, budget exhausted, or rate-limited. Everything below costs money.
        if not settings.transcription_youtube_audio_fallback_enabled:
            raise NoTranscriptAvailableError(
                f"No captions for {url} and the audio fallback is disabled"
            )
        if not settings.transcription_enabled:
            raise NoTranscriptAvailableError(
                f"No captions for {url} and transcription is disabled"
            )
        if workdir is None:
            raise NoTranscriptAvailableError(
                f"No captions for {url} and no workdir was provided for audio"
            )

        # Gate on YouTube's OWN metadata first — one cheap API call, no bytes
        # transferred. Downloading first would let an 8-hour livestream fill
        # the shared temp_uploads volume before being rejected, and that volume
        # is shared with the documents worker.
        duration = await probe_youtube_duration(url)
        if duration is not None:
            self._enforce_duration_bounds(url, duration)
        else:
            logger.warning(
                "Could not read duration metadata for %s; downloading without "
                "a length gate (size is still capped)",
                url,
            )

        logger.warning(
            "PAID PATH: downloading audio for caption-less YouTube video %s", url
        )
        audio_path = await download_youtube_audio(url, workdir)
        # Re-probe the downloaded file: it is the artifact actually being sent,
        # and its duration may differ from the advertised metadata.
        probe = await self.enforce_media_gates(str(audio_path))
        result = await self._transcribe_local_file(audio_path, workdir)
        if result.duration_seconds is None:
            result.duration_seconds = probe.duration_seconds or duration
        return result

    async def transcribe_media_url(
        self,
        url: str,
        *,
        workdir: Path | None = None,
    ) -> TranscriptResult:
        """Transcribe a direct audio/video URL. Always the paid path.

        Under ``url_direct`` (default) the worker never touches the file:
        the duration cap is enforced from the remote header and AssemblyAI
        fetches the media itself. Under ``preprocess`` the file is downloaded
        and conditioned first.
        """
        if not settings.transcription_enabled:
            raise NoTranscriptAvailableError(
                f"Transcription is disabled; skipping {url}"
            )

        # Cost guard first, in both modes. The probe is reused below so
        # size_bytes costs no extra call.
        probe = await self.enforce_media_gates(url)

        if settings.TRANSCRIPTION_AUDIO_MODE == "url_direct":
            logger.info(
                "PAID PATH (url_direct): handing %s to AssemblyAI, no download", url
            )
            result = await self._client.transcribe(url)
        else:
            if workdir is None:
                raise NoTranscriptAvailableError(
                    f"TRANSCRIPTION_AUDIO_MODE=preprocess requires a workdir for {url}"
                )
            logger.info("PAID PATH (preprocess): downloading %s", url)
            source = workdir / "source.media"
            await stream_download_to_disk(url, source)
            result = await self._transcribe_local_file(source, workdir)

        if result.duration_seconds is None:
            result.duration_seconds = probe.duration_seconds
        if result.source_size_bytes is None:
            result.source_size_bytes = probe.size_bytes
        return result

    async def _transcribe_local_file(
        self,
        path: Path,
        workdir: Path,
    ) -> TranscriptResult:
        """Condition (if enabled), upload, transcribe."""
        to_upload = path
        if settings.TRANSCRIPTION_AUDIO_MODE == "preprocess":
            to_upload = await preprocess_to_wav(path, workdir / "clean.wav")

        upload_url = await self._client.upload_file(to_upload)
        return await self._client.transcribe(upload_url)


# Module singleton — matches the service-layer convention used elsewhere.
transcription_service = TranscriptionService()
