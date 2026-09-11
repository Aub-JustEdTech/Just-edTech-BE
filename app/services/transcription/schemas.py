"""Transcript text format — the single source of truth for the stored transcript.

A transcript is persisted as ONE plain-text file. The format is deliberately
both human-readable and machine-parseable, because the two things the provider
returns alongside the words are not recoverable later:

* **timestamps** — without them "jump to 15:54" citations cannot exist;
* **speaker labels** — without them a board meeting reads as one anonymous
  wall of text.

Losing either means re-transcribing and paying again, so they are written into
the line prefix rather than into a sidecar file:

    # transcript-format: v2
    # source: assemblyai
    # language: en
    # duration_seconds: 280

    [00:00:00.000 - 00:00:07.143] Speaker A: Good evening, I call this to order.
    [00:00:07.143 - 00:00:12.500] Speaker B: Thank you. First item is the budget.

Both ends of each utterance are written, not just the start. Storing only the
start would force the reader to infer each end from the next line's start,
which silently swallows the silence between utterances and leaves the final
utterance with no end at all.

Timestamps are ``HH:MM:SS.mmm`` — millisecond precision, matching
``TranscriptSegment``, so a write/read round trip is lossless.

Writer and reader both go through ``to_text_document`` / ``from_text_document``
so the two cannot drift. ``from_envelope`` is retained read-only, to keep
transcripts written before this format was introduced parseable — nothing
writes JSON any more.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

ENVELOPE_VERSION = 1
TEXT_FORMAT_VERSION = "v2"

SOURCE_YOUTUBE_CAPTIONS = "youtube_captions"
SOURCE_ASSEMBLYAI = "assemblyai"

CAPTION_KIND_MANUAL = "manual"
CAPTION_KIND_AUTO = "auto"

# Header lines carrying document-level provenance. Kept in the file itself so a
# stored transcript is self-describing without a database lookup.
_HEADER_PREFIX = "#"
_HEADER_KEY_FORMAT = "transcript-format"

# ``[HH:MM:SS.mmm - HH:MM:SS.mmm] Speaker X: text``
#
# Hours are unbounded (``\d+``) because a multi-day stream is valid input.
# The speaker group is optional — YouTube captions carry no speaker labels.
# ``[^:]+`` for the label stops at the first colon, so an utterance whose text
# contains a colon ("the vote is: 5 to 2") still parses correctly.
_SEGMENT_RE = re.compile(
    r"^\[\s*(?P<sh>\d+):(?P<sm>\d{2}):(?P<ss>\d{2})\.(?P<sms>\d{3})"
    r"\s*-\s*"
    r"(?P<eh>\d+):(?P<em>\d{2}):(?P<es>\d{2})\.(?P<ems>\d{3})\s*\]"
    r"\s*(?:Speaker\s+(?P<speaker>[^:]+):)?"
    r"\s*(?P<text>.*)$"
)


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
        """Serialise to the legacy JSON envelope.

        No longer written to S3 — ``to_text_document`` is the stored format.
        Retained for the offline research scripts under ``scripts/school_data``,
        which dump JSON locally for inspection.
        """
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
        """Parse a legacy JSON envelope back into a result. READ-ONLY.

        Kept so transcripts stored before the text format was introduced stay
        readable and re-processable. Nothing writes this shape any more; a
        stored transcript is expensive and must never need re-transcribing
        just because the format moved on.

        Unknown future versions are read best-effort rather than rejected.
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

    def to_text_document(self) -> str:
        """Render to the stored text format. THE artifact — see module docstring.

        One line per utterance, so the reader can rely on line boundaries.
        Newlines inside an utterance are collapsed to spaces to guarantee that.
        """
        lines: list[str] = [f"{_HEADER_PREFIX} {_HEADER_KEY_FORMAT}: {TEXT_FORMAT_VERSION}"]

        # None-valued keys are omitted rather than written empty: the parser
        # defaults anything absent to None, so writing "# language:" would be
        # noise that round-trips to the same result.
        for key, value in (
            ("source", self.source),
            ("language", self.language),
            ("duration_seconds", self.duration_seconds),
            ("speech_model", self.speech_model),
            ("caption_kind", self.caption_kind),
            ("source_size_bytes", self.source_size_bytes),
        ):
            if value is not None and value != "":
                lines.append(f"{_HEADER_PREFIX} {key}: {value}")

        lines.append("")

        if not self.segments:
            # No timestamps to write. The body is still the transcript, and
            # from_text_document reads it back as flat text with zero segments,
            # which the chunker handles by falling through to the generic
            # chunker rather than producing nothing.
            lines.append(self.text.strip())
            return "\n".join(lines) + "\n"

        for seg in self.segments:
            stamp = f"{_format_timestamp(seg.start_ms)} - {_format_timestamp(seg.end_ms)}"
            body = " ".join(seg.text.split())
            if seg.speaker:
                lines.append(f"[{stamp}] Speaker {seg.speaker}: {body}")
            else:
                lines.append(f"[{stamp}] {body}")

        return "\n".join(lines) + "\n"

    @classmethod
    def from_text_document(cls, raw: str) -> TranscriptResult:
        """Parse the stored text format back into a result.

        Lenient by design: a stored transcript is expensive, so an unreadable
        line is folded into the preceding utterance rather than discarded, and
        a file with no parseable timestamps still yields its text.
        """
        header: dict[str, Any] = {}
        segments: list[TranscriptSegment] = []
        loose: list[str] = []

        for line in raw.splitlines():
            stripped = line.strip()
            if not stripped:
                continue

            # Header lines only count before the first utterance; a "#" inside
            # the transcript body is speech, not metadata.
            if stripped.startswith(_HEADER_PREFIX) and not segments and not loose:
                key, _, value = stripped.lstrip(_HEADER_PREFIX).strip().partition(":")
                key, value = key.strip(), value.strip()
                if key and value:
                    header[key] = value
                continue

            match = _SEGMENT_RE.match(stripped)
            if match:
                speaker = match.group("speaker")
                segments.append(
                    TranscriptSegment(
                        start_ms=_parse_timestamp(
                            match.group("sh"), match.group("sm"),
                            match.group("ss"), match.group("sms"),
                        ),
                        end_ms=_parse_timestamp(
                            match.group("eh"), match.group("em"),
                            match.group("es"), match.group("ems"),
                        ),
                        text=match.group("text").strip(),
                        speaker=speaker.strip() if speaker else None,
                    )
                )
            elif segments:
                # Continuation of the previous utterance (a stray newline, or a
                # line written by an older/hand-edited file).
                segments[-1].text = f"{segments[-1].text} {stripped}".strip()
            else:
                loose.append(stripped)

        text = (
            " ".join(s.text for s in segments if s.text)
            if segments
            else " ".join(loose)
        )

        def _int(key: str) -> int | None:
            value = header.get(key)
            if value is None:
                return None
            try:
                return int(value)
            except (TypeError, ValueError):
                return None

        return cls(
            source=header.get("source") or "",
            text=text,
            segments=segments,
            language=header.get("language"),
            duration_seconds=_int("duration_seconds"),
            speech_model=header.get("speech_model"),
            caption_kind=header.get("caption_kind"),
            source_size_bytes=_int("source_size_bytes"),
        )


def _format_timestamp(ms: int) -> str:
    """Milliseconds -> HH:MM:SS.mmm (millisecond precision, lossless)."""
    total_ms = max(0, ms)
    hours, rem = divmod(total_ms, 3_600_000)
    minutes, rem = divmod(rem, 60_000)
    seconds, millis = divmod(rem, 1000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}.{millis:03d}"


def _parse_timestamp(hours: str, minutes: str, seconds: str, millis: str) -> int:
    """HH:MM:SS.mmm components -> milliseconds."""
    return (
        int(hours) * 3_600_000
        + int(minutes) * 60_000
        + int(seconds) * 1000
        + int(millis)
    )
