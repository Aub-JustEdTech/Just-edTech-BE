"""Transcript processor — reads the stored text transcript, chunks on utterances.

``extract_text`` returns the flat prose so summarisation and classification
see clean text with no timestamp noise embedded in it. ``chunk_transcript``
produces the chunks that actually get embedded, each carrying the exact
``start_ms`` / ``end_ms`` / ``speaker`` of the utterances inside it.

The invariant that matters: **a segment is never split across chunks.**
Splitting mid-utterance would make a chunk's timestamps approximate, and an
approximate timestamp is a citation that jumps to the wrong moment.

The transcript arrives as the single ``.txt`` artifact written by the
transcription service (see ``TranscriptResult.to_text_document``), with
timestamps and speaker labels in each line prefix. Which processor runs is
decided by ``Document.document_type`` (``.transcript``), not by the S3 object's
name — so the stored file being a ``.txt`` changes nothing about routing.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from app.core.config import settings
from app.services.document_processing.base import DocumentProcessor
from app.services.transcription.schemas import TranscriptResult

logger = logging.getLogger(__name__)

_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")


def _split_oversized_segment(
    seg: Any, max_chars: int
) -> list[dict[str, Any]]:
    """Split a single segment far larger than ``max_chars`` on sentence
    boundaries, interpolating each piece's timestamps linearly by character
    offset within the segment's own ``[start_ms, end_ms]`` range.

    Happens when diarization collapses an entire long recording into one
    "utterance" (e.g. a shared boardroom mic feed with no detected speaker
    changes) — AssemblyAI gives no timing finer than that single range, so a
    piece's timestamp is an approximation, not an exact per-sentence value.
    Still far better than either failing the whole document (today's
    behaviour) or leaving every citation pointing at the same instant.
    """
    text = seg.text
    total_chars = len(text) or 1
    duration = seg.end_ms - seg.start_ms

    def interpolate(char_offset: int) -> int:
        return seg.start_ms + int(duration * char_offset / total_chars)

    pieces: list[dict[str, Any]] = []
    buffer = ""
    buffer_start_offset = 0
    offset = 0

    for sentence in _SENTENCE_SPLIT_RE.split(text):
        if not sentence:
            continue
        candidate = f"{buffer} {sentence}".strip() if buffer else sentence
        if buffer and len(candidate) > max_chars:
            pieces.append(
                {
                    "text": buffer.strip(),
                    "start_ms": interpolate(buffer_start_offset),
                    "end_ms": interpolate(offset),
                    "speaker": seg.speaker,
                    "timestamp_approximate": True,
                }
            )
            buffer_start_offset = offset
            buffer = sentence
        else:
            buffer = candidate
        offset += len(sentence) + 1

    if buffer:
        pieces.append(
            {
                "text": buffer.strip(),
                "start_ms": interpolate(buffer_start_offset),
                "end_ms": seg.end_ms,
                "speaker": seg.speaker,
                "timestamp_approximate": True,
            }
        )

    return pieces


class TranscriptProcessor(DocumentProcessor):
    """Process transcripts produced by the transcription service."""

    supported_extensions = [".transcript"]
    supported_mime_types = ["text/plain"]

    def _load(self, file_path: str) -> TranscriptResult:
        with open(file_path, encoding="utf-8") as fh:
            raw = fh.read()

        # Transcripts written before the text format was introduced are JSON
        # envelopes. Sniffing the first character keeps them re-processable
        # without a bulk S3 rewrite — re-transcribing them would mean paying
        # for words we already have. Remove once no .json transcripts remain.
        if raw.lstrip().startswith("{"):
            try:
                return TranscriptResult.from_envelope(json.loads(raw))
            except json.JSONDecodeError:
                # A text transcript whose first utterance happens to start with
                # "{" — fall through and parse it as text.
                logger.debug("Leading '{' was not valid JSON; parsing as text")

        return TranscriptResult.from_text_document(raw)

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
        """A transcript is valid when it carries content.

        Text parsing is deliberately lenient, so "does it parse" is no longer a
        useful question — almost anything parses. It cannot be stricter than
        this either: a caption-only transcript legitimately has no timestamp
        lines, so absence of segments is not corruption. An empty or
        whitespace-only file is, and that is the case worth catching.
        """
        try:
            result = self._load(file_path)
        except Exception:
            return False
        return bool(result.segments) or bool(result.text.strip())

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

        oversized_segments = 0
        for seg in result.segments:
            seg_chars = len(seg.text) + 1

            # A single segment far larger than the packing cap can't be
            # packed normally — most often a diarization failure that
            # collapsed hours of speech into one "utterance". Flush whatever
            # was buffered first, then split this segment on its own with
            # interpolated timestamps (see _split_oversized_segment).
            if seg_chars > max_chars:
                flush()
                chunks.extend(_split_oversized_segment(seg, max_chars))
                oversized_segments += 1
                continue

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

        if oversized_segments:
            logger.warning(
                "%s oversized segment(s) force-split with interpolated "
                "timestamps (likely a diarization failure) while chunking "
                "transcript",
                oversized_segments,
            )
        logger.info(
            "Chunked transcript into %s chunks from %s segments",
            len(chunks),
            len(result.segments),
        )
        return chunks
