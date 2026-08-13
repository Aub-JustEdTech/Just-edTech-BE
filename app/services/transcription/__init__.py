"""Audio/video transcription: YouTube captions (free) with AssemblyAI fallback."""

from app.services.transcription.exceptions import (
    MediaTooLargeError,
    MediaTooLongError,
    MediaUnavailableError,
    NoTranscriptAvailableError,
    TerminalTranscriptionError,
    TranscriptionError,
    TranscriptionProviderError,
)
from app.services.transcription.schemas import (
    ENVELOPE_VERSION,
    SOURCE_ASSEMBLYAI,
    SOURCE_YOUTUBE_CAPTIONS,
    TranscriptResult,
    TranscriptSegment,
)
from app.services.transcription.service import (
    TranscriptionService,
    transcription_service,
)

__all__ = [
    "ENVELOPE_VERSION",
    "SOURCE_ASSEMBLYAI",
    "SOURCE_YOUTUBE_CAPTIONS",
    "MediaTooLargeError",
    "MediaTooLongError",
    "MediaUnavailableError",
    "NoTranscriptAvailableError",
    "TerminalTranscriptionError",
    "TranscriptResult",
    "TranscriptSegment",
    "TranscriptionError",
    "TranscriptionProviderError",
    "TranscriptionService",
    "transcription_service",
]
