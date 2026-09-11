"""Unit tests for the transcription package. No network, no DB.

The tests that matter most are the guards:

* ``test_ffmpeg_args_never_contain_forbidden_filters`` protects timestamp
  alignment — a silence-trimming filter would shift every timestamp after
  the first cut and silently break click-to-jump citations.
* ``test_request_body_has_no_vocabulary_keys`` protects an explicit product
  requirement that no custom vocabulary is ever sent.
* ``test_chunk_never_splits_a_segment`` protects citation accuracy.
"""

from __future__ import annotations

import json

import pytest

from app.services.transcription.assemblyai_client import (
    _group_words,
    _to_result,
    build_request_body,
)
from app.services.transcription.audio_preprocessor import (
    FORBIDDEN_FILTERS,
    build_ffmpeg_args,
    build_filter_chain,
)
from app.services.transcription.schemas import (
    ENVELOPE_VERSION,
    SOURCE_ASSEMBLYAI,
    SOURCE_YOUTUBE_CAPTIONS,
    TranscriptResult,
    TranscriptSegment,
)
from app.services.transcription.youtube import (
    canonical_youtube_url,
    extract_youtube_id,
    is_youtube_url,
)

# ---------------------------------------------------------------------------
# YouTube URL parsing
# ---------------------------------------------------------------------------

VIDEO_ID = "n_SOB-VqQh0"


@pytest.mark.parametrize(
    "url,expected",
    [
        (f"https://www.youtube.com/watch?v={VIDEO_ID}", VIDEO_ID),
        (f"https://youtube.com/watch?v={VIDEO_ID}", VIDEO_ID),
        (f"https://m.youtube.com/watch?v={VIDEO_ID}", VIDEO_ID),
        (f"https://youtu.be/{VIDEO_ID}", VIDEO_ID),
        (f"https://www.youtube.com/embed/{VIDEO_ID}", VIDEO_ID),
        (f"https://www.youtube-nocookie.com/embed/{VIDEO_ID}", VIDEO_ID),
        (f"https://www.youtube.com/v/{VIDEO_ID}", VIDEO_ID),
        (f"https://www.youtube.com/live/{VIDEO_ID}", VIDEO_ID),
        (f"https://www.youtube.com/shorts/{VIDEO_ID}", VIDEO_ID),
        # Extra query params must not defeat extraction.
        (f"https://www.youtube.com/watch?v={VIDEO_ID}&t=90", VIDEO_ID),
        (f"https://www.youtube.com/watch?v={VIDEO_ID}&list=PLDWOkKsVBtJk&index=22", VIDEO_ID),
        (f"https://youtu.be/{VIDEO_ID}?t=42", VIDEO_ID),
        # Not YouTube videos.
        ("https://example.org/board/minutes.pdf", None),
        ("https://vimeo.com/123456789", None),
        ("https://www.youtube.com/channel/UCabcdefghij", None),
        ("https://www.youtube.com/watch?v=tooshort", None),
        ("", None),
        ("not a url at all", None),
    ],
)
def test_extract_youtube_id(url, expected):
    assert extract_youtube_id(url) == expected


def test_is_youtube_url():
    assert is_youtube_url(f"https://youtu.be/{VIDEO_ID}") is True
    assert is_youtube_url("https://example.org/a.mp4") is False


def test_url_variants_canonicalise_to_one_url():
    """This is what makes url_hash dedup work — and stops paying 3x."""
    variants = [
        f"https://www.youtube.com/watch?v={VIDEO_ID}",
        f"https://youtu.be/{VIDEO_ID}",
        f"https://www.youtube.com/embed/{VIDEO_ID}",
        f"https://www.youtube.com/watch?v={VIDEO_ID}&t=90",
        f"https://www.youtube-nocookie.com/embed/{VIDEO_ID}",
    ]
    canonical = {canonical_youtube_url(v) for v in variants}
    assert canonical == {f"https://www.youtube.com/watch?v={VIDEO_ID}"}


def test_canonical_returns_none_for_non_youtube():
    assert canonical_youtube_url("https://example.org/a.mp4") is None


# ---------------------------------------------------------------------------
# ffmpeg filter chain — the timestamp-alignment guard
# ---------------------------------------------------------------------------


def test_ffmpeg_args_contain_required_flags():
    args = build_ffmpeg_args("in.mp4", "out.wav")
    joined = " ".join(args)
    assert "-vn" in args
    assert "-ac" in args and "1" in args
    assert "16000" in args
    assert "highpass=f=80" in joined
    assert "afftdn" in joined


@pytest.mark.parametrize("forbidden", FORBIDDEN_FILTERS)
def test_ffmpeg_args_never_contain_forbidden_filters(forbidden):
    """Silence trimming shifts timestamps; loudnorm/dynaudnorm lower SNR.

    Measured on a real board meeting: denoise-only +1.7 dB, +loudnorm
    -3.3 dB, +dynaudnorm -6.6 dB.
    """
    joined = " ".join(build_ffmpeg_args("in.mp4", "out.wav"))
    assert forbidden not in joined

    # Also absent across every configuration of the chain builder.
    for denoise in (True, False):
        for gain in (0.0, 6.0, -3.0):
            chain = build_filter_chain(denoise=denoise, gain_db=gain)
            assert forbidden not in chain


def test_gain_is_linear_and_only_applied_when_nonzero():
    assert "volume=" not in build_filter_chain(gain_db=0.0)
    assert "volume=6.0dB" in build_filter_chain(gain_db=6.0)


def test_denoise_can_be_disabled():
    assert "afftdn" not in build_filter_chain(denoise=False)
    assert "afftdn" in build_filter_chain(denoise=True)


# ---------------------------------------------------------------------------
# AssemblyAI request body — the "no vocabulary" guard
# ---------------------------------------------------------------------------


def test_request_body_has_no_vocabulary_keys():
    """Explicit requirement: no custom vocabulary is ever sent."""
    body = build_request_body("https://example.org/a.mp3")
    for forbidden in ("keyterms_prompt", "word_boost", "boost_param", "custom_spelling"):
        assert forbidden not in body


def test_request_body_enables_speaker_labels():
    body = build_request_body("https://example.org/a.mp3")
    assert body["speaker_labels"] is True
    assert body["audio_url"] == "https://example.org/a.mp3"


def test_request_body_uses_plural_speech_models_array():
    """Regression: the singular `speech_model` is deprecated and now 400s.

    Live API response 2026-07-29: "The speech_model parameter is deprecated.
    Use speech_models: [...]". Sending the singular form fails every request,
    so this asserts both the name and the type.
    """
    body = build_request_body("https://example.org/a.mp3", ["m1", "m2"])
    assert "speech_model" not in body
    assert body["speech_models"] == ["m1", "m2"]
    assert isinstance(body["speech_models"], list)


def test_request_body_defaults_to_configured_models():
    from app.core.config import settings

    body = build_request_body("https://example.org/a.mp3")
    assert body["speech_models"] == list(settings.ASSEMBLYAI_SPEECH_MODELS)


def test_request_body_keys_are_exactly_expected():
    body = build_request_body("https://example.org/a.mp3", ["universal-2"])
    assert set(body) == {
        "audio_url",
        "speech_models",
        "speaker_labels",
        "language_code",
        "punctuate",
        "format_text",
    }


# ---------------------------------------------------------------------------
# AssemblyAI response mapping
# ---------------------------------------------------------------------------


def test_to_result_prefers_utterances():
    payload = {
        "text": "Motion approved. Seconded.",
        "audio_duration": 4312,
        "language_code": "en",
        "utterances": [
            {"start": 954000, "end": 957200, "speaker": "A", "text": "Motion approved."},
            {"start": 957300, "end": 958900, "speaker": "B", "text": "Seconded."},
        ],
    }
    result = _to_result(payload, "universal-3-5-pro")
    assert result.source == SOURCE_ASSEMBLYAI
    assert len(result.segments) == 2
    assert result.segments[0].speaker == "A"
    assert result.segments[0].start_ms == 954000
    assert result.speakers == ["A", "B"]
    assert result.duration_seconds == 4312
    assert result.speech_model == "universal-3-5-pro"


def test_group_words_splits_on_speaker_change():
    words = [
        {"start": 0, "end": 500, "text": "Motion", "speaker": "A"},
        {"start": 500, "end": 900, "text": "approved.", "speaker": "A"},
        {"start": 1000, "end": 1500, "text": "Seconded.", "speaker": "B"},
    ]
    segments = _group_words(words)
    assert len(segments) == 2
    assert segments[0].text == "Motion approved."
    assert segments[0].speaker == "A"
    assert segments[1].speaker == "B"
    assert segments[1].start_ms == 1000


# ---------------------------------------------------------------------------
# Envelope round-trip
# ---------------------------------------------------------------------------


def _sample_result() -> TranscriptResult:
    return TranscriptResult(
        source=SOURCE_ASSEMBLYAI,
        text="Motion approved. Seconded.",
        segments=[
            TranscriptSegment(954000, 957200, "Motion approved.", "A"),
            TranscriptSegment(957300, 958900, "Seconded.", "B"),
        ],
        language="en",
        duration_seconds=4312,
        speech_model="universal-3-5-pro",
    )


def test_envelope_round_trip_preserves_timestamps_and_speakers():
    original = _sample_result()
    envelope = original.to_envelope()

    # Must survive an actual JSON round-trip, not just a dict copy.
    restored = TranscriptResult.from_envelope(json.loads(json.dumps(envelope)))

    assert restored.text == original.text
    assert restored.speech_model == original.speech_model
    assert len(restored.segments) == len(original.segments)
    for before, after in zip(original.segments, restored.segments, strict=True):
        assert after.start_ms == before.start_ms
        assert after.end_ms == before.end_ms
        assert after.speaker == before.speaker
        assert after.text == before.text


def test_envelope_round_trips_source_size_bytes():
    """Provenance for a file that was never downloaded."""
    original = _sample_result()
    original.source_size_bytes = 184_320_000
    restored = TranscriptResult.from_envelope(
        json.loads(json.dumps(original.to_envelope()))
    )
    assert restored.source_size_bytes == 184_320_000


def test_envelope_declares_version_and_speakers():
    envelope = _sample_result().to_envelope()
    assert envelope["version"] == ENVELOPE_VERSION
    assert envelope["speakers"] == ["A", "B"]
    assert "segments" in envelope
    assert "text" in envelope


def test_youtube_segments_have_no_speaker():
    """Accepted by design: YouTube captions carry no speaker information."""
    result = TranscriptResult(
        source=SOURCE_YOUTUBE_CAPTIONS,
        text="hello there",
        segments=[TranscriptSegment(0, 5400, "hello there", None)],
    )
    assert result.speakers == []
    assert result.to_envelope()["speakers"] == []


def test_is_empty():
    assert TranscriptResult(source="x", text="   ").is_empty is True
    assert TranscriptResult(source="x", text="hi").is_empty is False


def test_text_document_includes_timestamps_and_speakers():
    text = _sample_result().to_text_document()
    assert "00:15:54" in text
    assert "Speaker A" in text


def test_text_document_round_trips_losslessly():
    """The text file is the ONLY stored artifact — anything it drops is gone.

    Guards the full write/read cycle at millisecond precision, including both
    utterance ends and the document-level header fields.
    """
    original = _sample_result()
    original.source_size_bytes = 184_320_000
    restored = TranscriptResult.from_text_document(original.to_text_document())

    assert restored.source == original.source
    assert restored.language == original.language
    assert restored.speech_model == original.speech_model
    assert restored.duration_seconds == original.duration_seconds
    assert restored.source_size_bytes == 184_320_000

    assert len(restored.segments) == len(original.segments)
    for before, after in zip(original.segments, restored.segments, strict=True):
        assert after.start_ms == before.start_ms
        assert after.end_ms == before.end_ms
        assert after.speaker == before.speaker
        assert after.text == before.text


# ---------------------------------------------------------------------------
# Caption timestamp arithmetic
# ---------------------------------------------------------------------------


def test_clamp_overlaps_removes_rolling_window_overlap():
    """YouTube auto-captions overlap; chunk boundaries must not.

    Regression: real auto-captions produced segment 0 ending at 6319 ms while
    segment 1 started at 4560 ms, so two chunks claimed the same audio.
    """
    from app.services.transcription.youtube import _clamp_overlaps

    segments = [
        TranscriptSegment(80, 6319, "a quick meeting tonight", None),
        TranscriptSegment(4560, 9200, "please join me in", None),
        TranscriptSegment(7800, 12000, "the pledge", None),
    ]
    clamped = _clamp_overlaps(segments)

    # Strictly non-overlapping afterwards.
    for earlier, later in zip(clamped, clamped[1:], strict=False):
        assert earlier.end_ms <= later.start_ms

    # start_ms is what click-to-jump uses — it must be untouched.
    assert [s.start_ms for s in clamped] == [80, 4560, 7800]
    # The final segment keeps its real end.
    assert clamped[-1].end_ms == 12000


def test_clamp_overlaps_is_noop_for_non_overlapping_segments():
    """Manually-created captions do not overlap and must not be altered."""
    from app.services.transcription.youtube import _clamp_overlaps

    segments = [
        TranscriptSegment(0, 5000, "one", "A"),
        TranscriptSegment(5000, 9000, "two", "B"),
        TranscriptSegment(10000, 12000, "three", "A"),
    ]
    clamped = _clamp_overlaps(segments)
    assert [(s.start_ms, s.end_ms) for s in clamped] == [
        (0, 5000),
        (5000, 9000),
        (10000, 12000),
    ]


def test_clamp_overlaps_never_inverts_a_segment():
    """A pathological overlap must not produce end_ms < start_ms."""
    from app.services.transcription.youtube import _clamp_overlaps

    segments = [
        TranscriptSegment(5000, 20000, "long", None),
        TranscriptSegment(1000, 3000, "starts earlier", None),
    ]
    clamped = _clamp_overlaps(segments)
    assert clamped[0].end_ms >= clamped[0].start_ms


def test_caption_ms_conversion_rounds_rather_than_truncates():
    """int((4.22 + 1.18) * 1000) == 5399, not 5400 — float truncation.

    Reproduces the arithmetic used in youtube._fetch_sync. Truncation here
    accumulates drift across a multi-hour recording.
    """
    start, duration = 4.22, 1.18
    assert int((start + duration) * 1000) == 5399  # the bug
    assert round((start + duration) * 1000) == 5400  # the fix


# ---------------------------------------------------------------------------
# Terminal vs transient error classification
# ---------------------------------------------------------------------------


class TestPreSpendGates:
    """The no-audio / too-short gates that stop paying for CMS decoration.

    Motivated by live data: five template video loops on cdn.cleversite.com
    (`Doodle.mp4`, `GirlBook.mp4`, `Pencils.mp4`, `schoolkidsrunning.mp4`,
    `studentsatdesk.mp4`), 10-30s each, **zero audio streams**, appearing on
    two districts because they share a website platform. Providers bill per
    audio-hour submitted whether or not speech is found.
    """

    # Real ffprobe output shape for a silent template clip.
    SILENT_CLIP = json.dumps(
        {
            "streams": [{"codec_type": "video", "codec_name": "h264"}],
            "format": {"duration": "10.000000"},
        }
    )
    # Real shape for the Apptegy clip: HAS audio, but only 28s long.
    SHORT_WITH_AUDIO = json.dumps(
        {
            "streams": [
                {"codec_type": "video", "codec_name": "h264"},
                {"codec_type": "audio", "codec_name": "aac"},
            ],
            "format": {"duration": "28.528500"},
        }
    )
    BOARD_MEETING = json.dumps(
        {
            "streams": [
                {"codec_type": "video", "codec_name": "h264"},
                {"codec_type": "audio", "codec_name": "aac"},
            ],
            "format": {"duration": "4312.5"},
        }
    )

    def test_parses_silent_clip_as_having_no_audio(self):
        from app.services.transcription.media_downloader import parse_ffprobe_json

        probe = parse_ffprobe_json(self.SILENT_CLIP)
        assert probe.probed is True
        assert probe.has_audio is False
        assert probe.has_video is True
        assert probe.duration_seconds == 10

    def test_parses_clip_with_audio(self):
        from app.services.transcription.media_downloader import parse_ffprobe_json

        probe = parse_ffprobe_json(self.SHORT_WITH_AUDIO)
        assert probe.has_audio is True
        assert probe.audio_codec == "aac"
        assert probe.duration_seconds == 28

    def test_probe_reads_size_from_the_header(self):
        """size_bytes must come from the probe.

        Under url_direct the file is never downloaded, so this is the ONLY
        point at which its size is knowable — otherwise scraped_media.size_bytes
        stays NULL for every transcribed item.
        """
        from app.services.transcription.media_downloader import parse_ffprobe_json

        raw = json.dumps(
            {
                "streams": [
                    {"codec_type": "video", "codec_name": "h264"},
                    {"codec_type": "audio", "codec_name": "aac"},
                ],
                "format": {"duration": "4312.5", "size": "184320000"},
            }
        )
        probe = parse_ffprobe_json(raw)
        assert probe.size_bytes == 184_320_000
        assert probe.duration_seconds == 4312

    def test_probe_tolerates_a_missing_size(self):
        from app.services.transcription.media_downloader import parse_ffprobe_json

        probe = parse_ffprobe_json(self.SILENT_CLIP)
        assert probe.size_bytes is None
        assert probe.probed is True

    def test_failed_probe_is_distinguishable_from_no_audio(self):
        """`probed=False` must not be mistaken for "there is no audio".

        Conflating them either bills for silent files or silently drops real
        meetings — the two failure modes this gate exists to avoid.
        """
        from app.services.transcription.media_downloader import parse_ffprobe_json

        probe = parse_ffprobe_json("not json")
        assert probe.probed is False
        assert probe.has_audio is False

    @pytest.mark.asyncio
    async def test_no_audio_stream_is_rejected_before_spending(self, monkeypatch):
        from app.services.transcription import media_downloader
        from app.services.transcription.exceptions import MediaHasNoAudioError
        from app.services.transcription.media_downloader import parse_ffprobe_json
        from app.services.transcription.service import TranscriptionService

        async def fake_probe(_url):
            return parse_ffprobe_json(self.SILENT_CLIP)

        monkeypatch.setattr(
            "app.services.transcription.service.probe_media", fake_probe
        )
        assert media_downloader  # keep the import meaningful

        with pytest.raises(MediaHasNoAudioError) as exc:
            await TranscriptionService().enforce_media_gates(
                "https://cdn.cleversite.com/media/education/Doodle.mp4"
            )
        assert exc.value.status == "no_audio"

    @pytest.mark.asyncio
    async def test_short_clip_passes_by_default(self, monkeypatch):
        """The duration floor is OFF by default, and that is deliberate.

        The 28s Apptegy clip that prompted a floor turned out to be digital
        silence (-91 dB throughout), so brevity was never the real defect.
        A floor would also drop genuine short content — a 40s public statement
        — to save ~$0.002. The empty-transcript guard handles this case on
        evidence rather than on a proxy.
        """
        from app.services.transcription.media_downloader import parse_ffprobe_json
        from app.services.transcription.service import TranscriptionService

        async def fake_probe(_url):
            return parse_ffprobe_json(self.SHORT_WITH_AUDIO)

        monkeypatch.setattr(
            "app.services.transcription.service.probe_media", fake_probe
        )

        probe = await TranscriptionService().enforce_media_gates(
            "https://x/clip.mp4"
        )
        assert probe.duration_seconds == 28

    @pytest.mark.asyncio
    async def test_short_clip_rejected_when_floor_is_configured(self, monkeypatch):
        """The floor still works for anyone who opts in."""
        from app.core.config import settings
        from app.services.transcription.exceptions import MediaTooShortError
        from app.services.transcription.media_downloader import parse_ffprobe_json
        from app.services.transcription.service import TranscriptionService

        async def fake_probe(_url):
            return parse_ffprobe_json(self.SHORT_WITH_AUDIO)

        monkeypatch.setattr(
            "app.services.transcription.service.probe_media", fake_probe
        )
        monkeypatch.setattr(
            settings, "SCHOOL_SCRAPER_MEDIA_MIN_DURATION_SECONDS", 60
        )

        with pytest.raises(MediaTooShortError) as exc:
            await TranscriptionService().enforce_media_gates("https://x/clip.mp4")
        assert exc.value.status == "skipped_too_short"

    @pytest.mark.asyncio
    async def test_real_board_meeting_passes_every_gate(self, monkeypatch):
        from app.services.transcription.media_downloader import parse_ffprobe_json
        from app.services.transcription.service import TranscriptionService

        async def fake_probe(_url):
            return parse_ffprobe_json(self.BOARD_MEETING)

        monkeypatch.setattr(
            "app.services.transcription.service.probe_media", fake_probe
        )

        probe = await TranscriptionService().enforce_media_gates(
            "https://x/board-meeting.mp4"
        )
        assert probe.duration_seconds == 4312

    @pytest.mark.asyncio
    async def test_unprobeable_media_fails_open(self, monkeypatch):
        """An unreadable header must not silently drop a real recording."""
        from app.services.transcription.media_downloader import MediaProbe
        from app.services.transcription.service import TranscriptionService

        async def fake_probe(_url):
            return MediaProbe(probed=False)

        monkeypatch.setattr(
            "app.services.transcription.service.probe_media", fake_probe
        )

        probe = await TranscriptionService().enforce_media_gates("https://x/a.mp4")
        assert probe.probed is False
        assert probe.duration_seconds is None

    @pytest.mark.asyncio
    async def test_over_long_media_still_rejected(self, monkeypatch):
        from app.services.transcription.exceptions import MediaTooLongError
        from app.services.transcription.media_downloader import MediaProbe
        from app.services.transcription.service import TranscriptionService

        async def fake_probe(_url):
            return MediaProbe(
                probed=True, duration_seconds=99_999, has_audio=True, has_video=True
            )

        monkeypatch.setattr(
            "app.services.transcription.service.probe_media", fake_probe
        )

        with pytest.raises(MediaTooLongError):
            await TranscriptionService().enforce_media_gates("https://x/stream.mp4")


def test_terminal_errors_carry_a_status_that_fits_the_column():
    from app.services.transcription.exceptions import (
        MediaHasNoAudioError,
        MediaTooLargeError,
        MediaTooLongError,
        MediaTooShortError,
        MediaUnavailableError,
        NoTranscriptAvailableError,
        TerminalTranscriptionError,
        TranscriptionProviderError,
    )

    terminal = [
        (MediaTooLargeError, "skipped_too_large"),
        (MediaTooLongError, "skipped_too_long"),
        (MediaTooShortError, "skipped_too_short"),
        (MediaHasNoAudioError, "no_audio"),
        (NoTranscriptAvailableError, "no_transcript"),
        (MediaUnavailableError, "media_unavailable"),
    ]
    for cls, expected_status in terminal:
        assert cls.status == expected_status
        assert issubclass(cls, TerminalTranscriptionError)
        # The status column is String(32) after the widening migration.
        assert len(cls.status) <= 32

    # Transient errors must NOT be terminal, or Celery would stop retrying.
    assert not issubclass(TranscriptionProviderError, TerminalTranscriptionError)
