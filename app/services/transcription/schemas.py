"""Transcript envelope — the single source of truth for the stored format.

The transcript is persisted as JSON, never as flat text or Markdown, because
flattening irreversibly destroys the two things the provider returns alongside
the words:

* **timestamps** — without them "jump to 15:54" citations cannot exist;
* **speaker labels** — without them a board meeting reads as one anonymous
  wall of text.

Neither is recoverable later: storing flat text now and wanting timestamps
afterwards means re-transcribing and paying again. The ``text`` field holds
exactly what a ``.md`` file would have held, so nothing is given up by
keeping the structure.

Writer and reader both go through ``to_envelope`` / ``from_envelope`` so the
two cannot drift.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

ENVELOPE_VERSION = 1

SOURCE_YOUTUBE_CAPTIONS = "youtube_captions"
SOURCE_ASSEMBLYAI = "assemblyai"

CAPTION_KIND_MANUAL = "manual"
CAPTION_KIND_AUTO = "auto"


@dataclass(slots=True)
class TranscriptSegment:
    """One utterance. Times are milliseconds from the start of the media."""

    start_ms: int
    end_ms: int
    text: str
    speaker: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "start_ms": self.start_ms,
            "end_ms": self.end_ms,
            "speaker": self.speaker,
            "text": self.text,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> TranscriptSegment:
        return cls(
            start_ms=int(raw["start_ms"]),
            end_ms=int(raw["end_ms"]),
            text=str(raw.get("text") or ""),
            speaker=raw.get("speaker"),
        )


@dataclass(slots=True)
class TranscriptResult:
    """A complete transcript plus the provenance needed to audit its cost."""

    source: str
    text: str
    segments: list[TranscriptSegment] = field(default_factory=list)
    language: str | None = None
    duration_seconds: int | None = None
    speech_model: str | None = None
    caption_kind: str | None = None
    # Byte size of the SOURCE media, read from its container header during the
    # pre-spend probe. Recorded here because under url_direct the file is never
    # downloaded, so this is the only point at which the size is known.
    source_size_bytes: int | None = None

    @property
    def speakers(self) -> list[str]:
        """Distinct speaker labels, ordered by first appearance."""
        seen: list[str] = []
        for seg in self.segments:
            if seg.speaker and seg.speaker not in seen:
                seen.append(seg.speaker)
        return seen

    @property
    def is_empty(self) -> bool:
        return not self.text.strip()

    def to_envelope(self) -> dict[str, Any]:
        """Serialise to the on-disk / S3 envelope."""
        return {
            "version": ENVELOPE_VERSION,
            "source": self.source,
            "language": self.language,
            "duration_seconds": self.duration_seconds,
            "speech_model": self.speech_model,
            "caption_kind": self.caption_kind,
            "source_size_bytes": self.source_size_bytes,
            "speakers": self.speakers,
            "text": self.text,
            "segments": [s.to_dict() for s in self.segments],
        }

    @classmethod
    def from_envelope(cls, envelope: dict[str, Any]) -> TranscriptResult:
        """Parse an envelope back into a result.

        Unknown future versions are read on a best-effort basis rather than
        rejected — a stored transcript is expensive and must stay readable.
        """
        return cls(
            source=str(envelope.get("source") or ""),
            text=str(envelope.get("text") or ""),
            segments=[
                TranscriptSegment.from_dict(s)
                for s in (envelope.get("segments") or [])
            ],
            language=envelope.get("language"),
            duration_seconds=envelope.get("duration_seconds"),
            speech_model=envelope.get("speech_model"),
            caption_kind=envelope.get("caption_kind"),
            source_size_bytes=envelope.get("source_size_bytes"),
        )

    def to_plain_text(self) -> str:
        """Human-readable rendering, written alongside the JSON envelope.

        This is a convenience export only. It is never the sole artifact —
        see the module docstring.
        """
        lines: list[str] = []
        for seg in self.segments:
            stamp = _format_timestamp(seg.start_ms)
            if seg.speaker:
                lines.append(f"[{stamp}] Speaker {seg.speaker}: {seg.text}")
            else:
                lines.append(f"[{stamp}] {seg.text}")
        return "\n".join(lines) if lines else self.text


def _format_timestamp(ms: int) -> str:
    """Milliseconds -> HH:MM:SS."""
    total_seconds = max(0, ms) // 1000
    hours, rem = divmod(total_seconds, 3600)
    minutes, seconds = divmod(rem, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
