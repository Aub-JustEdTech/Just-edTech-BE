"""Transcription exceptions.

Two families, and the distinction drives retry behaviour in the Celery task:

* ``TerminalTranscriptionError`` — deterministic. Retrying re-does the same
  work and fails identically, so the task records ``.status`` and returns
  WITHOUT raising. Retrying a 4 GB file three times is pure waste.
* ``TranscriptionProviderError`` — transient (5xx, timeout, rate limit).
  Propagates so Celery retries with exponential backoff.

These are worker-internal and are deliberately NOT wired into the FastAPI
exception handlers — nothing here is ever raised inside a request cycle.
"""

from __future__ import annotations


class TranscriptionError(Exception):
    """Base class for all transcription failures."""


class TerminalTranscriptionError(TranscriptionError):
    """A failure that retrying cannot fix.

    ``status`` is written verbatim to ``ScrapedMedia.status``; it must fit
    the column width (32) and be stable enough to filter on later.
    """

    status: str = "failed"


class MediaTooLargeError(TerminalTranscriptionError):
    """Media exceeds SCHOOL_SCRAPER_MEDIA_MAX_DOWNLOAD_MB."""

    status = "skipped_too_large"


class MediaTooLongError(TerminalTranscriptionError):
    """Media exceeds SCHOOL_SCRAPER_MEDIA_MAX_DURATION_MINUTES.

    Raised BEFORE any spend — duration comes from a remote header read.
    """

    status = "skipped_too_long"


class MediaHasNoAudioError(TerminalTranscriptionError):
    """The file carries no audio stream, so there is nothing to transcribe.

    Real case that motivated this: school CMS templates ship decorative video
    loops (``Doodle.mp4``, ``Pencils.mp4``) with **no audio track at all**.
    They look like meeting recordings to a scraper. Verified on live sites —
    5 such files, 10-30s each, zero audio streams, served from one CDN and
    appearing on every district using that platform.

    Providers bill per audio-hour submitted whether or not speech is found,
    so without this gate the template layer of every school website is a
    recurring charge that returns nothing.
    """

    status = "no_audio"


class MediaTooShortError(TerminalTranscriptionError):
    """Media is shorter than SCHOOL_SCRAPER_MEDIA_MIN_DURATION_SECONDS.

    Catches decorative clips that DO have a silent or music-only audio track,
    which the no-audio check cannot see. A board meeting runs for hours; a
    template loop runs for seconds.
    """

    status = "skipped_too_short"


class NoTranscriptAvailableError(TerminalTranscriptionError):
    """No transcript could be produced (no captions and no usable audio)."""

    status = "no_transcript"


class MediaUnavailableError(TerminalTranscriptionError):
    """Source media cannot be fetched: 404, private, age-restricted, removed."""

    status = "media_unavailable"


class TranscriptionProviderError(TranscriptionError):
    """Transient provider/transport failure. Propagates so Celery retries."""
