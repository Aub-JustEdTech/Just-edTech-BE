"""Preview transcripts for confirmed schools' audio/video/YouTube media.

Replaces the old `scripts/school_data/generate_transcripts.py` one-off: the
confirmed URL to scrape now comes from the DB (`School` + `SchoolScrapeUrl`)
instead of a static JSON file, via `crud.schools.list_active_scrape_urls`.

Nothing here reimplements transcription: it calls the same
`app.services.transcription.service.transcription_service` used by the
production `ingest_scraped_media` task, so cost gates, model fallback and
speaker labels are identical. The difference is that nothing is persisted —
no `scraped_media` rows, no S3, no Qdrant — this is a review/cost-preview
tool. Use `/school-scraper/scrape-media` or `/scrape-all` to actually ingest.

Cost model — the same gates as production, cheapest first:
  * YouTube WITH captions (manual or auto) -> free
  * duration over the cap                  -> skipped before any spend
  * everything else                        -> AssemblyAI, ~$0.23/audio-hour
"""

from __future__ import annotations

import asyncio
import logging
import shutil
import tempfile
import time
from collections import Counter
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.crud.schools import list_active_scrape_urls
from app.models.school import SchoolScrapeUrl
from app.schemas.school_scraper import (
    TranscriptPreviewItem,
    TranscriptPreviewRequest,
    TranscriptPreviewResponse,
)
from app.services.transcription.exceptions import TerminalTranscriptionError
from app.services.transcription.schemas import SOURCE_ASSEMBLYAI
from app.services.transcription.service import transcription_service
from app.services.web_scraper.school_scraper_service import SchoolScraperService

logger = logging.getLogger(__name__)

AV_MEDIA_TYPES = ("audio", "video", "youtube")
USD_PER_AUDIO_HOUR = 0.23


class TranscriptPreviewService:
    async def preview(
        self,
        db: AsyncSession,
        tenant_id: int,
        request: TranscriptPreviewRequest,
    ) -> TranscriptPreviewResponse:
        scrape_urls = await list_active_scrape_urls(
            db,
            tenant_id,
            school_ids=request.school_ids,
            org_codes=request.org_codes,
        )
        if not scrape_urls:
            return TranscriptPreviewResponse(
                dry_run=request.dry_run, schools_scraped=0, total_av_items_found=0, items=[]
            )

        entries, scrape_failures = await self._collect_av_items(scrape_urls)

        if request.youtube_only:
            entries = [e for e in entries if e["item"].get("media_type") == "youtube"]
        if request.limit_items:
            entries = entries[: request.limit_items]

        if request.dry_run:
            return TranscriptPreviewResponse(
                dry_run=True,
                schools_scraped=len(scrape_urls),
                total_av_items_found=len(entries),
                items=[self._preview_item(e) for e in entries],
                scrape_failures=scrape_failures,
            )

        Path(settings.SCHOOL_SCRAPER_MEDIA_TEMP_DIR).mkdir(parents=True, exist_ok=True)
        results = await self._transcribe_all(entries, request.concurrency)

        statuses = Counter(r.status for r in results)
        return TranscriptPreviewResponse(
            dry_run=False,
            schools_scraped=len(scrape_urls),
            total_av_items_found=len(entries),
            items=results,
            statuses=dict(statuses),
            free_count=sum(1 for r in results if r.status == "ok" and not r.paid),
            paid_count=sum(1 for r in results if r.paid),
            estimated_total_usd=round(sum(r.estimated_usd for r in results), 4),
            scrape_failures=scrape_failures,
        )

    async def _collect_av_items(
        self, scrape_urls: list[SchoolScrapeUrl]
    ) -> tuple[list[dict[str, Any]], list[str]]:
        """Scrape every confirmed URL, sequentially, reusing one browser
        instance across all of them (mirrors `_sweep_school_media_async`)."""
        entries: list[dict[str, Any]] = []
        failures: list[str] = []

        async with SchoolScraperService() as scraper:
            for scrape_url in scrape_urls:
                school = scrape_url.school
                try:
                    result = await scraper.scrape_media_files(
                        page_url=scrape_url.url, crawl_depth=scrape_url.crawl_depth
                    )
                except Exception as exc:  # noqa: BLE001 — one bad site must not stop the rest
                    logger.warning(
                        "Transcript preview scrape failed for %s: %s", scrape_url.url, exc
                    )
                    failures.append(scrape_url.url)
                    continue

                for item in result.get("media_files", []):
                    if item.get("media_type") in AV_MEDIA_TYPES:
                        entries.append({"school": school, "item": item})

        return entries, failures

    def _preview_item(self, entry: dict[str, Any]) -> TranscriptPreviewItem:
        school, item = entry["school"], entry["item"]
        return TranscriptPreviewItem(
            school_id=school.id,
            school_name=school.name,
            org_code=school.org_code,
            media_type=item.get("media_type"),
            media_url=str(item.get("url")),
            media_name=item.get("name"),
            source_page_url=str(item.get("source_page_url")),
            status="preview",
        )

    async def _transcribe_all(
        self, entries: list[dict[str, Any]], concurrency: int
    ) -> list[TranscriptPreviewItem]:
        semaphore = asyncio.Semaphore(concurrency)

        async def one(entry: dict[str, Any]) -> TranscriptPreviewItem:
            async with semaphore:
                return await self._transcribe_one(entry)

        return list(await asyncio.gather(*(one(e) for e in entries)))

    async def _transcribe_one(self, entry: dict[str, Any]) -> TranscriptPreviewItem:
        school, item = entry["school"], entry["item"]
        result_item = TranscriptPreviewItem(
            school_id=school.id,
            school_name=school.name,
            org_code=school.org_code,
            media_type=item.get("media_type"),
            media_url=str(item.get("url")),
            media_name=item.get("name"),
            source_page_url=str(item.get("source_page_url")),
            status="pending",
        )

        workdir = Path(tempfile.mkdtemp(dir=settings.SCHOOL_SCRAPER_MEDIA_TEMP_DIR))
        started = time.monotonic()
        try:
            url = str(item.get("url"))
            if item.get("media_type") == "youtube":
                transcript = await transcription_service.transcribe_youtube(url, workdir=workdir)
            else:
                transcript = await transcription_service.transcribe_media_url(url, workdir=workdir)

            if transcript.is_empty:
                result_item.status = "no_transcript"
                result_item.error = "transcript was empty"
                return result_item

            result_item.status = "ok"
            result_item.source = transcript.source
            result_item.caption_kind = transcript.caption_kind
            result_item.speech_model = transcript.speech_model
            result_item.duration_seconds = transcript.duration_seconds
            result_item.segments = len(transcript.segments)
            result_item.speakers = transcript.speakers
            result_item.paid = transcript.source == SOURCE_ASSEMBLYAI
            result_item.text_preview = transcript.text[:500]
            if result_item.paid and transcript.duration_seconds:
                result_item.estimated_usd = round(
                    transcript.duration_seconds / 3600 * USD_PER_AUDIO_HOUR, 4
                )
        except TerminalTranscriptionError as exc:
            # Deterministic: the same status the Celery ingest task would record.
            result_item.status = exc.status
            result_item.error = str(exc)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Transcript preview failed for %s", item.get("url"))
            result_item.status = "failed"
            result_item.error = f"{type(exc).__name__}: {exc}"
        finally:
            result_item.elapsed_seconds = round(time.monotonic() - started, 1)
            shutil.rmtree(workdir, ignore_errors=True)

        return result_item


transcript_preview_service = TranscriptPreviewService()
