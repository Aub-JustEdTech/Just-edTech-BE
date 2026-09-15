"""Per-tenant transcription budget — the guard between an upload form and a metered API.

The gates in ``TranscriptionService`` protect against *one* bad item: no audio,
too long, unreadable. None of them stop a tenant uploading two hundred good
one-hour recordings. That is the exposure this module closes.

Two calls, deliberately separate:

* ``assert_within_quota`` runs BEFORE spending, against the media's probed
  duration. It is the only thing that can prevent a charge.
* ``record_usage`` runs AFTER, against the duration actually transcribed.

They are separate because the pre-spend number is an estimate from a container
header and the post-spend number is what the provider billed. Collapsing them
into one call would mean either checking the quota against a number that does
not exist yet, or recording a charge that never happened.
"""

from __future__ import annotations

import logging
from datetime import date
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.media_transcription_usage import MediaTranscriptionUsage

logger = logging.getLogger(__name__)


class MediaQuotaExceededError(Exception):
    """Raised when a tenant's monthly transcription budget is exhausted."""

    def __init__(self, message: str, *, used_minutes: int, limit_minutes: int):
        super().__init__(message)
        self.used_minutes = used_minutes
        self.limit_minutes = limit_minutes


def _current_month() -> date:
    today = date.today()
    return date(today.year, today.month, 1)


def _estimate_cost_usd(duration_seconds: int) -> Decimal:
    hours = Decimal(duration_seconds) / Decimal(3600)
    rate = Decimal(str(settings.TRANSCRIPTION_COST_PER_AUDIO_HOUR_USD))
    return (hours * rate).quantize(Decimal("0.000001"))


class MediaUsageService:
    """Monthly transcription budget accounting, per tenant."""

    async def get_month_usage_seconds(
        self,
        db: AsyncSession,
        tenant_id: int,
        month: date | None = None,
    ) -> int:
        """Billable seconds transcribed for this tenant this month."""
        result = await db.execute(
            select(func.coalesce(func.sum(MediaTranscriptionUsage.duration_seconds), 0))
            .where(MediaTranscriptionUsage.tenant_id == tenant_id)
            .where(MediaTranscriptionUsage.usage_month == (month or _current_month()))
            .where(MediaTranscriptionUsage.billable.is_(True))
        )
        return int(result.scalar_one() or 0)

    async def assert_within_quota(
        self,
        db: AsyncSession,
        tenant_id: int,
        additional_seconds: int = 0,
    ) -> None:
        """Raise if this tenant cannot afford ``additional_seconds`` more.

        ``additional_seconds`` may be 0 when the duration is not yet known —
        the check then degrades to "is the tenant already over", which still
        stops the runaway case even though it cannot stop a single overshoot.
        """
        limit_minutes = settings.TENANT_MEDIA_MONTHLY_MINUTES_LIMIT
        if limit_minutes <= 0:
            return  # cap disabled

        used_seconds = await self.get_month_usage_seconds(db, tenant_id)
        projected = used_seconds + max(0, additional_seconds)
        limit_seconds = limit_minutes * 60

        if projected > limit_seconds:
            used_minutes = used_seconds // 60
            logger.warning(
                "Tenant %s transcription quota exceeded: %s min used + %s s "
                "requested against a %s min cap",
                tenant_id,
                used_minutes,
                additional_seconds,
                limit_minutes,
            )
            raise MediaQuotaExceededError(
                f"Monthly transcription limit reached: {used_minutes} of "
                f"{limit_minutes} minutes used. Contact your administrator to "
                f"raise the limit.",
                used_minutes=used_minutes,
                limit_minutes=limit_minutes,
            )

    async def record_usage(
        self,
        db: AsyncSession,
        *,
        tenant_id: int,
        source: str,
        duration_seconds: int | None,
        billable: bool,
        document_id: int | None = None,
        provider: str | None = None,
        speech_model: str | None = None,
    ) -> MediaTranscriptionUsage:
        """Log one transcription. Free items are logged too — see module docstring."""
        seconds = int(duration_seconds or 0)
        usage = MediaTranscriptionUsage(
            tenant_id=tenant_id,
            document_id=document_id,
            source=source,
            provider=provider,
            speech_model=speech_model,
            duration_seconds=seconds,
            billable=billable,
            estimated_cost_usd=_estimate_cost_usd(seconds) if billable else Decimal(0),
            usage_month=_current_month(),
        )
        db.add(usage)
        await db.flush()
        logger.info(
            "Recorded %s transcription for tenant %s: %ss (billable=%s)",
            source,
            tenant_id,
            seconds,
            billable,
        )
        return usage


media_usage_service = MediaUsageService()
