"""Transcript processor — reads the JSON envelope, chunks on utterances.

``extract_text`` returns the flat prose so summarisation and classification
see clean text with no timestamp noise embedded in it. ``chunk_transcript``
produces the chunks that actually get embedded, each carrying the exact
``start_ms`` / ``end_ms`` / ``speaker`` of the utterances inside it.

The invariant that matters: **a segment is never split across chunks.**
Splitting mid-utterance would make a chunk's timestamps approximate, and an
approximate timestamp is a citation that jumps to the wrong moment.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from app.core.config import settings
from app.services.document_processing.base import DocumentProcessor
from app.services.transcription.schemas import TranscriptResult

logger = logging.getLogger(__name__)


class TranscriptProcessor(DocumentProcessor):
    """Process transcript envelopes produced by the transcription service."""

    supported_extensions = [".transcript"]
    supported_mime_types = ["application/json"]

    def _load(self, file_path: str) -> TranscriptResult:
        with open(file_path, encoding="utf-8") as fh:
            envelope = json.load(fh)
        return TranscriptResult.from_envelope(envelope)

    def extract_text(self, file_path: str) -> str:
        """Flat prose — no timestamps, no speaker prefixes."""
        try:
            result = self._load(file_path)
            logger.info(
                "Extracted %s characters from transcript (%s segments, source=%s)",
                len(result.text),
                len(result.segments),
                result.source,
            )
            return result.text.strip()
        except Exception as e:
            logger.error(f"Error extracting text from transcript: {e}")
            raise

    def extract_metadata(self, file_path: str) -> dict[str, Any]:
        """Envelope provenance. Keys here land on every chunk payload."""
        try:
            result = self._load(file_path)
            return {
                "transcript_source": result.source,
                "language": result.language,
                "duration_seconds": result.duration_seconds,
                "speech_model": result.speech_model,
                "caption_kind": result.caption_kind,
                "speaker_count": len(result.speakers),
                "segment_count": len(result.segments),
            }
        except Exception as e:
            logger.error(f"Error extracting transcript metadata: {e}")
            return {}

    def validate(self, file_path: str) -> bool:
        try:
            self._load(file_path)
            return True
        except Exception:
            return False

    def chunk_transcript(self, file_path: str) -> list[dict[str, Any]]:
        """Pack consecutive segments into chunks on utterance boundaries.

        A chunk closes when adding the next segment would exceed either the
        target duration or the character cap — but a segment already started
        is always finished, so boundaries stay exact.
        """
        result = self._load(file_path)
        if not result.segments:
            return []

        target_ms = settings.TRANSCRIPTION_CHUNK_TARGET_SECONDS * 1000
        max_chars = settings.TRANSCRIPTION_CHUNK_MAX_CHARS

        chunks: list[dict[str, Any]] = []
        buffer: list[str] = []
        buf_chars = 0
        start_ms: int | None = None
        end_ms = 0
        speakers: list[str] = []

        def flush() -> None:
            nonlocal buffer, buf_chars, start_ms, end_ms, speakers
            if not buffer or start_ms is None:
                return
            chunks.append(
                {
                    "text": " ".join(buffer).strip(),
                    "start_ms": start_ms,
                    "end_ms": end_ms,
                    # A chunk spanning several speakers reports them joined,
                    # so a citation can still say who was talking.
                    "speaker": ", ".join(speakers) if speakers else None,
                }
            )
            buffer = []
            buf_chars = 0
            start_ms = None
            end_ms = 0
            speakers = []

        for seg in result.segments:
            seg_chars = len(seg.text) + 1
            would_overflow = buffer and (
                (seg.end_ms - start_ms) > target_ms
                or (buf_chars + seg_chars) > max_chars
            )
            if would_overflow:
                flush()

            if start_ms is None:
                start_ms = seg.start_ms
            buffer.append(seg.text)
            buf_chars += seg_chars
            end_ms = seg.end_ms
            if seg.speaker and seg.speaker not in speakers:
                speakers.append(seg.speaker)

        flush()

        logger.info(
            "Chunked transcript into %s chunks from %s segments",
            len(chunks),
            len(result.segments),
        )
        return chunks
