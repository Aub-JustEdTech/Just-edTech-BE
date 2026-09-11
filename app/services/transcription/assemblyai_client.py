"""AssemblyAI REST client (async, no SDK).

The official SDK is deliberately not used: it is sync-only, which would force
``asyncio.to_thread`` around a multi-hour poll inside an async Celery task,
and its model enum lags new IDs such as ``universal-3-5-pro``. ``httpx`` is
already a dependency and the REST surface here is three endpoints.

Authentication uses the raw key in the ``authorization`` header — AssemblyAI
does NOT use a ``Bearer`` prefix.

**No custom vocabulary is ever sent.** ``keyterms_prompt``, ``word_boost`` and
``custom_spelling`` are all absent from the request body by explicit product
requirement, and ``build_request_body`` is exposed specifically so a test can
assert they stay absent.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx

from app.core.config import settings
from app.services.transcription.exceptions import (
    MediaUnavailableError,
    TranscriptionProviderError,
)
from app.services.transcription.schemas import (
    SOURCE_ASSEMBLYAI,
    TranscriptResult,
    TranscriptSegment,
)

logger = logging.getLogger(__name__)

_UPLOAD_CHUNK_BYTES = 1024 * 1024  # 1 MiB


def build_request_body(
    audio_url: str, speech_models: list[str] | None = None
) -> dict[str, Any]:
    """The exact transcription request body.

    ``speech_models`` is a PLURAL ARRAY, and the ordering is the fallback
    preference — AssemblyAI walks it server-side and uses the first model
    available for the account and language. The singular ``speech_model`` was
    deprecated and is now rejected outright:

        "The speech_model parameter is deprecated. Use speech_models:
         [\"universal-3-5-pro\", \"universal-2\"] ..."

    Exposed as a pure function so tests can assert the invariants:
    ``speaker_labels`` is on, the models field is a list, and no vocabulary
    key is present.
    """
    return {
        "audio_url": audio_url,
        "speech_models": list(speech_models or settings.ASSEMBLYAI_SPEECH_MODELS),
        "speaker_labels": settings.ASSEMBLYAI_SPEAKER_LABELS,
        "language_code": settings.ASSEMBLYAI_LANGUAGE_CODE,
        "punctuate": True,
        "format_text": True,
    }


class AssemblyAIClient:
    """Thin async wrapper over the AssemblyAI transcription API."""

    def __init__(self, api_key: str | None = None, base_url: str | None = None):
        self.api_key = api_key or settings.ASSEMBLYAI_API_KEY
        self.base_url = (base_url or settings.ASSEMBLYAI_BASE_URL).rstrip("/")

    @property
    def _headers(self) -> dict[str, str]:
        # Raw key, no "Bearer" prefix.
        return {"authorization": self.api_key}

    def _require_key(self) -> None:
        if not self.api_key:
            raise TranscriptionProviderError(
                "ASSEMBLYAI_API_KEY is not set; cannot transcribe"
            )

    async def upload_file(self, path: Path) -> str:
        """Upload a local file and return the AssemblyAI-hosted URL.

        Streams from disk via an async generator — a multi-hour WAV is never
        read into memory.
        """
        self._require_key()

        async def _chunks() -> AsyncIterator[bytes]:
            with path.open("rb") as fh:
                while True:
                    chunk = fh.read(_UPLOAD_CHUNK_BYTES)
                    if not chunk:
                        break
                    yield chunk

        async with httpx.AsyncClient(timeout=None) as client:
            try:
                resp = await client.post(
                    f"{self.base_url}/v2/upload",
                    headers=self._headers,
                    content=_chunks(),
                )
                resp.raise_for_status()
            except httpx.HTTPError as exc:
                raise TranscriptionProviderError(
                    f"AssemblyAI upload failed for {path.name}: {exc}"
                ) from exc

        upload_url = resp.json().get("upload_url")
        if not upload_url:
            raise TranscriptionProviderError(
                "AssemblyAI upload returned no upload_url"
            )
        return upload_url

    async def transcribe(
        self,
        audio_url: str,
        *,
        speech_models: list[str] | None = None,
    ) -> TranscriptResult:
        """Transcribe ``audio_url``.

        The model fallback is handled SERVER-SIDE: ``speech_models`` is sent as
        an ordered array and AssemblyAI picks the first one available for the
        account and language. There is deliberately no client-side loop — the
        deprecated singular ``speech_model`` was what required one.

        Which model actually ran comes back in the response, so a silent
        downgrade is visible in the stored envelope rather than invisible.
        """
        self._require_key()
        models = list(speech_models or settings.ASSEMBLYAI_SPEECH_MODELS)

        transcript_id = await self._submit(audio_url, models)
        payload = await self._poll(transcript_id)

        used = payload.get("speech_model") or (models[0] if models else None)
        if models and used and used != models[0]:
            logger.warning(
                "AssemblyAI used speech_model=%s, not the preferred %s. Verify "
                "the preferred model is enabled for this account — transcripts "
                "are running on a fallback.",
                used,
                models[0],
            )
        return _to_result(payload, used)

    async def _submit(self, audio_url: str, speech_models: list[str]) -> str:
        body = build_request_body(audio_url, speech_models)
        async with httpx.AsyncClient(timeout=60.0) as client:
            try:
                resp = await client.post(
                    f"{self.base_url}/v2/transcript",
                    headers=self._headers,
                    json=body,
                )
            except httpx.HTTPError as exc:
                raise TranscriptionProviderError(
                    f"AssemblyAI submit failed: {exc}"
                ) from exc

        if resp.status_code == 400:
            # A malformed request — a bad model ID, or a parameter the API has
            # deprecated. Terminal: retrying sends the identical body.
            raise TranscriptionProviderError(
                f"AssemblyAI rejected the request (HTTP 400): {resp.text[:400]}"
            )
        if resp.status_code in (401, 403):
            raise TranscriptionProviderError(
                f"AssemblyAI rejected the API key (HTTP {resp.status_code})"
            )
        if resp.status_code >= 500:
            raise TranscriptionProviderError(
                f"AssemblyAI server error HTTP {resp.status_code}"
            )
        if resp.status_code >= 400:
            raise TranscriptionProviderError(
                f"AssemblyAI submit returned HTTP {resp.status_code}: {resp.text[:300]}"
            )

        transcript_id = resp.json().get("id")
        if not transcript_id:
            raise TranscriptionProviderError("AssemblyAI submit returned no id")
        return transcript_id

    async def _poll(self, transcript_id: str) -> dict[str, Any]:
        url = f"{self.base_url}/v2/transcript/{transcript_id}"
        deadline = settings.ASSEMBLYAI_POLL_TIMEOUT_SECONDS
        interval = settings.ASSEMBLYAI_POLL_INTERVAL_SECONDS
        waited = 0

        async with httpx.AsyncClient(timeout=60.0) as client:
            while waited < deadline:
                try:
                    resp = await client.get(url, headers=self._headers)
                    resp.raise_for_status()
                except httpx.HTTPError as exc:
                    raise TranscriptionProviderError(
                        f"AssemblyAI poll failed for {transcript_id}: {exc}"
                    ) from exc

                payload = resp.json()
                status = payload.get("status")

                if status == "completed":
                    return payload
                if status == "error":
                    error = str(payload.get("error") or "unknown error")
                    # A source the provider cannot fetch will never succeed.
                    if _is_unfetchable(error):
                        raise MediaUnavailableError(
                            f"AssemblyAI could not fetch the media: {error}"
                        )
                    raise TranscriptionProviderError(
                        f"AssemblyAI transcription failed: {error}"
                    )

                await asyncio.sleep(interval)
                waited += interval

        raise TranscriptionProviderError(
            f"AssemblyAI poll timed out after {deadline}s for {transcript_id}"
        )


def _is_unfetchable(error: str) -> bool:
    lowered = error.lower()
    return any(
        marker in lowered
        for marker in ("download error", "not found", "404", "unable to fetch")
    )


def _to_result(payload: dict[str, Any], speech_model: str | None) -> TranscriptResult:
    """Map an AssemblyAI response onto the envelope.

    Prefers ``utterances`` — present because ``speaker_labels=True`` — since
    they are already grouped per speaker turn. Falls back to grouping ``words``
    when a response lacks them.
    """
    segments: list[TranscriptSegment] = []

    utterances = payload.get("utterances") or []
    if utterances:
        for utt in utterances:
            segments.append(
                TranscriptSegment(
                    start_ms=int(utt.get("start") or 0),
                    end_ms=int(utt.get("end") or 0),
                    text=str(utt.get("text") or "").strip(),
                    speaker=utt.get("speaker"),
                )
            )
    else:
        segments = _group_words(payload.get("words") or [])

    duration_ms = payload.get("audio_duration")
    duration_seconds = (
        int(duration_ms) if isinstance(duration_ms, int | float) else None
    )

    return TranscriptResult(
        source=SOURCE_ASSEMBLYAI,
        text=str(payload.get("text") or "").strip(),
        segments=[s for s in segments if s.text],
        language=payload.get("language_code"),
        duration_seconds=duration_seconds,
        speech_model=speech_model,
    )


def _group_words(words: list[dict[str, Any]]) -> list[TranscriptSegment]:
    """Group a flat word list into segments on speaker change."""
    segments: list[TranscriptSegment] = []
    current: dict[str, Any] | None = None

    for word in words:
        speaker = word.get("speaker")
        if current is None or current["speaker"] != speaker:
            if current is not None:
                segments.append(
                    TranscriptSegment(
                        start_ms=current["start"],
                        end_ms=current["end"],
                        text=" ".join(current["words"]).strip(),
                        speaker=current["speaker"],
                    )
                )
            current = {
                "speaker": speaker,
                "start": int(word.get("start") or 0),
                "end": int(word.get("end") or 0),
                "words": [],
            }
        current["end"] = int(word.get("end") or current["end"])
        current["words"].append(str(word.get("text") or ""))

    if current is not None:
        segments.append(
            TranscriptSegment(
                start_ms=current["start"],
                end_ms=current["end"],
                text=" ".join(current["words"]).strip(),
                speaker=current["speaker"],
            )
        )

    return segments
