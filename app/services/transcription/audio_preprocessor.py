"""ffmpeg audio conditioning — used ONLY when TRANSCRIPTION_AUDIO_MODE=preprocess.

The filter chain is deliberately conservative:

    highpass=f=80,afftdn=nf=-25[,volume=<gain>dB]

* ``highpass`` removes HVAC rumble below speech.
* ``afftdn`` is spectral denoise. Measured +1.7 dB SNR on a real board meeting.
* ``volume`` is LINEAR gain, appended only when gain != 0.

What is deliberately absent, and why it must stay absent:

* ``loudnorm`` / ``dynaudnorm`` — compression lifts background noise more than
  speech. Measured on the same recording: loudnorm −3.3 dB SNR, dynaudnorm
  −6.6 dB. They make the audio *worse* for recognition, not better.
* ``silenceremove`` / ``atrim`` — these shift the timeline. Every timestamp
  after the first cut would point at the wrong moment in the source video,
  silently breaking click-to-jump citations. They would save ~13% of cost and
  cost the entire feature.

``FORBIDDEN_FILTERS`` plus its unit test is the regression guard for that.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from app.core.config import settings
from app.services.transcription.exceptions import TranscriptionProviderError

logger = logging.getLogger(__name__)

# Never allowed in the chain — see module docstring. Asserted by test.
FORBIDDEN_FILTERS = ("silenceremove", "atrim", "loudnorm", "dynaudnorm")


def build_filter_chain(
    *,
    highpass_hz: int | None = None,
    denoise: bool | None = None,
    gain_db: float | None = None,
) -> str:
    """Build the ``-af`` filter string. Pure function, so it is testable."""
    hp = settings.TRANSCRIPTION_HIGHPASS_HZ if highpass_hz is None else highpass_hz
    use_denoise = (
        settings.TRANSCRIPTION_DENOISE_ENABLED if denoise is None else denoise
    )
    gain = settings.TRANSCRIPTION_GAIN_DB if gain_db is None else gain_db

    filters = [f"highpass=f={hp}"]
    if use_denoise:
        filters.append("afftdn=nf=-25")
    if gain:
        # Linear gain. Never a compressor — see module docstring.
        filters.append(f"volume={gain}dB")
    return ",".join(filters)


def build_ffmpeg_args(src: str, dst: str, **chain_kwargs) -> list[str]:
    """Full argv for the conditioning pass. Pure function, so it is testable."""
    return [
        settings.TRANSCRIPTION_FFMPEG_PATH,
        "-hide_banner",
        "-nostdin",
        "-y",
        "-i",
        src,
        "-vn",
        "-map",
        "0:a:0",
        "-af",
        build_filter_chain(**chain_kwargs),
        "-ac",
        "1",
        "-ar",
        str(settings.TRANSCRIPTION_SAMPLE_RATE_HZ),
        "-c:a",
        "pcm_s16le",
        dst,
    ]


async def preprocess_to_wav(src: Path, dst: Path, **chain_kwargs) -> Path:
    """Run the conditioning pass. Returns ``dst`` on success."""
    args = build_ffmpeg_args(str(src), str(dst), **chain_kwargs)
    dst.parent.mkdir(parents=True, exist_ok=True)

    logger.info("Preprocessing audio: %s -> %s", src.name, dst.name)

    try:
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError as exc:
        raise TranscriptionProviderError(
            f"ffmpeg not found at {settings.TRANSCRIPTION_FFMPEG_PATH}"
        ) from exc

    try:
        _stdout, stderr = await asyncio.wait_for(
            proc.communicate(),
            timeout=settings.TRANSCRIPTION_FFMPEG_TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError as exc:
        proc.kill()
        await proc.wait()
        raise TranscriptionProviderError(
            f"ffmpeg timed out after {settings.TRANSCRIPTION_FFMPEG_TIMEOUT_SECONDS}s "
            f"on {src.name}"
        ) from exc

    if proc.returncode != 0:
        # Keep the stderr tail so error_message is actually diagnosable.
        tail = stderr.decode("utf-8", errors="replace")[-1000:]
        raise TranscriptionProviderError(
            f"ffmpeg failed (rc={proc.returncode}) on {src.name}: {tail}"
        )

    return dst
