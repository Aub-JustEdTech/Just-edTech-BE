"""Supadata — tier 2 of the YouTube transcript fallback.

When ``youtube-transcript-api`` has no captions, or YouTube blocks the
request outright, this fetches the transcript from Supadata instead of
downloading the video ourselves. Supadata does its own audio extraction
server-side (native captions first, ASR fallback), so YouTube's bot-detection
becomes Supadata's infrastructure problem, not ours — the failure mode this
replaces (yt-dlp getting 403'd or IP-blocked, even with a PO Token, JS
runtime and proxy in front of it) cannot happen here because we never talk
to YouTube directly for this step.

Ported from the POC in ``scripts/school_data/supadata_trial.py`` once that
trial confirmed the integration works; the standalone script is retired.

Same "absent vs unreachable" discipline as ``youtube.py``: a transcript that
genuinely doesn't exist (HTTP 206) returns ``None`` so the caller marks the
item terminal. A rate limit / quota error also returns ``None`` rather than
raising — Supadata's free-tier credits are limited, so a Celery retry storm
against a quota error would burn budget for nothing. Only unexpected
transport/5xx failures raise, so Celery retries those with backoff.
"""

from __future__ import annotations

import asyncio
import logging
import time

import httpx

from app.core.config import settings
from app.services.transcription.exceptions import TranscriptionProviderError
from app.services.transcription.schemas import (
    SOURCE_SUPADATA,
    TranscriptResult,
    TranscriptSegment,
)

logger = logging.getLogger(__name__)

_POLL_INTERVAL_SECONDS = 5
_POLL_TIMEOUT_SECONDS = 300
_REQUEST_TIMEOUT_SECONDS = 60.0


async def fetch_supadata_transcript(url: str) -> TranscriptResult | None:
    """Fetch a YouTube transcript from Supadata.

    Returns ``None`` when Supadata has genuinely nothing for this video, or
    when we're rate-limited/over quota (logged distinctly either way — see
    module docstring). Raises ``TranscriptionProviderError`` on unexpected
    transport failures so Celery retries with backoff.
    """
    if not settings.SCHOOL_SCRAPER_SUPADATA_ENABLED:
        return None
    if not settings.SUPADATA_API_KEY:
        logger.warning("SUPADATA_API_KEY is not set; skipping Supadata for %s", url)
        return None

    headers = {"x-api-key": settings.SUPADATA_API_KEY}
    params = {
        "url": url,
        "mode": "auto",
        # False keeps per-segment offset/duration timestamps; True would
        # flatten to one string and throw away click-to-jump timing.
        "text": "false",
        "lang": "en",
    }

    async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT_SECONDS) as client:
        try:
            response = await client.get(
                f"{settings.SUPADATA_BASE_URL}/transcript",
                params=params,
                headers=headers,
            )
        except httpx.HTTPError as exc:
            raise TranscriptionProviderError(
                f"Supadata request failed for {url}: {type(exc).__name__}: {exc}"
            ) from exc

        if response.status_code == 202:
            polled = await _poll_job(client, response, url, headers)
            if polled is None:
                return None
            response = polled

        if response.status_code == 206:
            logger.warning(
                "Supadata has no transcript for %s (206) — captions genuinely "
                "absent from both youtube-transcript-api and Supadata.",
                url,
            )
            return None

        if response.status_code == 429:
            logger.warning(
                "Supadata rate-limited/quota-exceeded for %s (429) — treating "
                "as no transcript for this run rather than retrying, to avoid "
                "burning limited credits.",
                url,
            )
            return None

        if response.status_code != 200:
            body_preview = response.text[:500] if response.text else ""
            raise TranscriptionProviderError(
                f"Supadata returned {response.status_code} for {url}: {body_preview}"
            )

        return _parse_success(response, url)


async def _poll_job(
    client: httpx.AsyncClient,
    response: httpx.Response,
    url: str,
    headers: dict[str, str],
) -> httpx.Response | None:
    """Long videos go async (202 + jobId) rather than returning inline."""
    try:
        job_id = response.json()["jobId"]
    except Exception as exc:  # noqa: BLE001
        raise TranscriptionProviderError(
            f"Supadata returned 202 with no parseable jobId for {url}: {exc}"
        ) from exc

    deadline = time.monotonic() + _POLL_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        await asyncio.sleep(_POLL_INTERVAL_SECONDS)
        poll = await client.get(
            f"{settings.SUPADATA_BASE_URL}/transcript/{job_id}",
            headers=headers,
        )
        body = poll.json() if poll.content else {}
        job_status = body.get("status")
        if job_status == "completed":
            return poll
        if job_status == "failed":
            logger.warning(
                "Supadata job %s failed for %s — treating as no transcript.",
                job_id,
                url,
            )
            return None

    logger.warning(
        "Supadata job %s for %s did not complete within %ss — treating as "
        "no transcript rather than retrying.",
        job_id,
        url,
        _POLL_TIMEOUT_SECONDS,
    )
    return None


def _parse_success(response: httpx.Response, url: str) -> TranscriptResult | None:
    body = response.json() if response.content else {}
    content = body.get("content") or []
    if not isinstance(content, list) or not content:
        logger.warning(
            "Supadata returned 200 for %s but no segments — treating as no "
            "transcript.",
            url,
        )
        return None

    segments = [
        TranscriptSegment(
            start_ms=int(item.get("offset") or 0),
            end_ms=int(item.get("offset") or 0) + int(item.get("duration") or 0),
            text=str(item.get("text") or "").strip(),
            speaker=None,
        )
        for item in content
    ]
    segments = [s for s in segments if s.text]
    if not segments:
        logger.warning(
            "Supadata returned an empty transcript for %s — treating as no "
            "transcript.",
            url,
        )
        return None

    duration_seconds = round(segments[-1].end_ms / 1000)

    logger.info(
        "Fetched %s Supadata segments for %s (lang=%s)",
        len(segments),
        url,
        body.get("lang"),
    )

    return TranscriptResult(
        source=SOURCE_SUPADATA,
        text=" ".join(s.text for s in segments),
        segments=segments,
        language=body.get("lang"),
        duration_seconds=duration_seconds,
        speech_model=None,
        caption_kind=None,
    )
