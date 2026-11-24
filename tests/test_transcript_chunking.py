"""Tests for TranscriptProcessor and the pipeline blockers it depends on.

The chunking invariant under test — a segment is never split — is what keeps
each chunk's start_ms/end_ms exact. An approximate timestamp is a citation
that jumps to the wrong moment in the recording.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.core.config import settings
from app.services.document_processing.factory import ProcessorFactory
from app.services.document_processing.processors.transcript_processor import (
    TranscriptProcessor,
)
from app.services.transcription.schemas import (
    SOURCE_ASSEMBLYAI,
    TranscriptResult,
    TranscriptSegment,
)


def _result(segments, **kwargs) -> TranscriptResult:
    return TranscriptResult(
        source=kwargs.pop("source", SOURCE_ASSEMBLYAI),
        text=" ".join(s.text for s in segments),
        segments=segments,
        **kwargs,
    )


def _write_transcript(tmp_path: Path, segments, **kwargs) -> str:
    """Write the stored text format — what the pipeline actually reads."""
    path = tmp_path / "transcript.transcript"
    path.write_text(_result(segments, **kwargs).to_text_document(), encoding="utf-8")
    return str(path)


def _write_legacy_envelope(tmp_path: Path, segments, **kwargs) -> str:
    """Write the pre-v2 JSON envelope, to prove old transcripts stay readable."""
    path = tmp_path / "legacy.transcript"
    path.write_text(
        json.dumps(_result(segments, **kwargs).to_envelope()), encoding="utf-8"
    )
    return str(path)


def _segments(count: int, seconds_each: int = 10) -> list[TranscriptSegment]:
    return [
        TranscriptSegment(
            start_ms=i * seconds_each * 1000,
            end_ms=(i + 1) * seconds_each * 1000,
            text=f"Utterance number {i}.",
            speaker="A" if i % 2 == 0 else "B",
        )
        for i in range(count)
    ]


# ---------------------------------------------------------------------------
# Factory registration — BUG-1's blast radius
# ---------------------------------------------------------------------------


def test_factory_resolves_transcript_extension():
    processor = ProcessorFactory.get_processor("abc123.transcript")
    assert isinstance(processor, TranscriptProcessor)


def test_factory_requires_the_leading_dot():
    """BUG-1: document_type="txt" produced "abc123txt", whose suffix is "".

    Path("abc123txt").suffix == "" and the factory is keyed on ".txt", so
    every school-scraper document raised before reaching text extraction.
    """
    assert Path("abc123txt").suffix == ""
    assert Path("abc123.txt").suffix == ".txt"

    with pytest.raises(ValueError) as exc:
        ProcessorFactory.get_processor("abc123txt")
    # The message must name the offending value, or this is undiagnosable.
    assert "abc123txt" in str(exc.value)


def test_factory_error_lists_supported_types():
    with pytest.raises(ValueError) as exc:
        ProcessorFactory.get_processor("thing.exe")
    assert ".transcript" in str(exc.value)
    assert ".pdf" in str(exc.value)


# ---------------------------------------------------------------------------
# Extraction
# ---------------------------------------------------------------------------


def test_extract_text_returns_flat_prose_without_timestamps(tmp_path):
    path = _write_transcript(tmp_path, _segments(3))
    text = TranscriptProcessor().extract_text(path)
    assert "Utterance number 0." in text
    # Timestamps must NOT be inlined — they would be embedded and degrade
    # retrieval, and the summarizer would see the noise.
    assert "00:00" not in text
    assert "start_ms" not in text


def test_extract_metadata_carries_provenance(tmp_path):
    path = _write_transcript(
        tmp_path,
        _segments(4),
        language="en",
        duration_seconds=40,
        speech_model="universal-3-5-pro",
    )
    meta = TranscriptProcessor().extract_metadata(path)
    assert meta["transcript_source"] == SOURCE_ASSEMBLYAI
    assert meta["speech_model"] == "universal-3-5-pro"
    assert meta["segment_count"] == 4
    assert meta["speaker_count"] == 2


def test_validate(tmp_path):
    good = _write_transcript(tmp_path, _segments(1))
    assert TranscriptProcessor().validate(good) is True

    # An empty file is the real corruption case. Text parsing is lenient by
    # design, so "does it parse" cannot be the test: a caption-only transcript
    # legitimately has no timestamp lines.
    empty = tmp_path / "empty.transcript"
    empty.write_text("   \n\n", encoding="utf-8")
    assert TranscriptProcessor().validate(str(empty)) is False


def test_validate_accepts_untimed_transcript(tmp_path):
    """Captions without cue times are a transcript, not a broken file."""
    untimed = tmp_path / "untimed.transcript"
    untimed.write_text("just the words, no timestamps", encoding="utf-8")
    assert TranscriptProcessor().validate(str(untimed)) is True


# ---------------------------------------------------------------------------
# Stored text format — the artifact chunking reads
# ---------------------------------------------------------------------------


def test_text_format_round_trip_is_millisecond_exact(tmp_path):
    """Both ends of every utterance survive a write/read cycle exactly.

    Storing only the start would force the reader to infer each end from the
    next line's start, swallowing inter-utterance silence and leaving the final
    utterance without an end.
    """
    segments = [
        TranscriptSegment(0, 7143, "Good evening.", "A"),
        TranscriptSegment(7143, 12500, "The vote is: 5 to 2.", "B"),
        TranscriptSegment(3_661_001, 3_670_999, "Over an hour in.", None),
    ]
    path = _write_transcript(tmp_path, segments, language="en", duration_seconds=3671)
    parsed = TranscriptProcessor()._load(path)

    assert [(s.start_ms, s.end_ms, s.speaker) for s in parsed.segments] == [
        (0, 7143, "A"),
        (7143, 12500, "B"),
        (3_661_001, 3_670_999, None),
    ]
    # A colon inside speech must not be mistaken for the speaker delimiter.
    assert parsed.segments[1].text == "The vote is: 5 to 2."
    assert parsed.language == "en"
    assert parsed.duration_seconds == 3671


def test_text_format_is_human_readable(tmp_path):
    path = _write_transcript(tmp_path, _segments(2))
    body = Path(path).read_text(encoding="utf-8")
    assert "[00:00:00.000 - 00:00:10.000] Speaker A: Utterance number 0." in body


def test_legacy_json_envelope_is_still_readable(tmp_path):
    """Transcripts stored before v2 must not need re-transcribing."""
    segments = _segments(12)
    path = _write_legacy_envelope(tmp_path, segments, language="en")
    chunks = TranscriptProcessor().chunk_transcript(path)

    assert chunks
    assert chunks[0]["start_ms"] == segments[0].start_ms
    assert chunks[-1]["end_ms"] == segments[-1].end_ms


# ---------------------------------------------------------------------------
# Chunking — the citation-accuracy guard
# ---------------------------------------------------------------------------


def test_chunk_never_splits_a_segment(tmp_path):
    segments = _segments(40)
    path = _write_transcript(tmp_path, segments)
    chunks = TranscriptProcessor().chunk_transcript(path)

    assert chunks
    # Every chunk boundary must coincide with a real segment boundary.
    starts = {s.start_ms for s in segments}
    ends = {s.end_ms for s in segments}
    for chunk in chunks:
        assert chunk["start_ms"] in starts
        assert chunk["end_ms"] in ends


def test_chunk_boundaries_are_monotonic_and_contiguous(tmp_path):
    path = _write_transcript(tmp_path, _segments(40))
    chunks = TranscriptProcessor().chunk_transcript(path)

    for chunk in chunks:
        assert chunk["start_ms"] < chunk["end_ms"]
    for earlier, later in zip(chunks, chunks[1:], strict=False):
        assert earlier["end_ms"] <= later["start_ms"]


def test_chunk_covers_every_segment_exactly_once(tmp_path):
    segments = _segments(37)
    path = _write_transcript(tmp_path, segments)
    chunks = TranscriptProcessor().chunk_transcript(path)

    assert chunks[0]["start_ms"] == segments[0].start_ms
    assert chunks[-1]["end_ms"] == segments[-1].end_ms

    rebuilt = " ".join(c["text"] for c in chunks)
    for seg in segments:
        assert seg.text in rebuilt


def test_chunk_respects_target_duration(tmp_path):
    """Chunks close near the target, never wildly beyond it."""
    path = _write_transcript(tmp_path, _segments(60, seconds_each=10))
    chunks = TranscriptProcessor().chunk_transcript(path)

    target_ms = settings.TRANSCRIPTION_CHUNK_TARGET_SECONDS * 1000
    for chunk in chunks[:-1]:
        span = chunk["end_ms"] - chunk["start_ms"]
        # One segment of overshoot is expected — a segment is never split.
        assert span <= target_ms + 10_000


def test_chunk_records_speakers_present_in_the_chunk(tmp_path):
    path = _write_transcript(tmp_path, _segments(10))
    chunks = TranscriptProcessor().chunk_transcript(path)
    assert any(c["speaker"] for c in chunks)


def test_chunk_handles_speakerless_youtube_segments(tmp_path):
    segments = [
        TranscriptSegment(i * 5000, (i + 1) * 5000, f"caption {i}", None)
        for i in range(12)
    ]
    path = _write_transcript(tmp_path, segments, source="youtube_captions")
    chunks = TranscriptProcessor().chunk_transcript(path)

    assert chunks
    # No speaker information, but timestamps are still exact.
    assert all(c["speaker"] is None for c in chunks)
    assert chunks[0]["start_ms"] == 0


def test_chunk_of_empty_transcript_is_empty(tmp_path):
    path = _write_transcript(tmp_path, [])
    assert TranscriptProcessor().chunk_transcript(path) == []


def test_single_oversized_segment_still_produces_one_chunk(tmp_path):
    """A segment longer than the target is emitted whole, not dropped."""
    long_text = "word " * 3000
    segments = [TranscriptSegment(0, 600_000, long_text.strip(), "A")]
    path = _write_transcript(tmp_path, segments)
    chunks = TranscriptProcessor().chunk_transcript(path)

    assert len(chunks) == 1
    assert chunks[0]["start_ms"] == 0
    assert chunks[0]["end_ms"] == 600_000
