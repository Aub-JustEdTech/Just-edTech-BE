#!/usr/bin/env python3
"""
Generate transcripts for every audio/video/YouTube item found on the districts'
confirmed meeting-archive URLs, using the project's own transcription service.

Nothing here reimplements transcription: it calls
`app.services.transcription.service.transcription_service`, so the cost gates,
the model fallback, speaker labels and the JSON envelope are exactly what the
Celery ingest path uses. The only difference is that output lands on disk for
review instead of S3 + Qdrant.

Cost model — the same three gates as production, cheapest first:
  * YouTube WITH captions (manual or auto) -> free
  * duration over the cap                  -> skipped before any spend
  * everything else                        -> AssemblyAI, ~$0.23/audio-hour

Run --dry-run first. It scrapes and lists exactly what would be transcribed,
with a cost estimate, and spends nothing.

MUST run inside the container: needs Playwright (most district A/V sits behind
JS-rendered widgets and is invisible without it), ffprobe, and the AssemblyAI
key. `./scripts` is mounted read-only, so write elsewhere and copy out:

    docker compose exec celery-scraper python \\
        scripts/school_data/generate_transcripts.py --dry-run

    docker compose exec celery-scraper python \\
        scripts/school_data/generate_transcripts.py --output-dir /tmp/transcripts

    docker compose cp celery-scraper:/tmp/transcripts \\
        scripts/school_data/output/transcripts

Other usage:
    --org-code 03480000     one district only
    --limit-items 3         cap total items (spend control while testing)
    --youtube-only          free path only, guaranteed $0
    --resume                skip items whose transcript file already exists
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import shutil
import sys
import tempfile
import time
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.core.config import settings
from app.services.transcription.exceptions import TerminalTranscriptionError
from app.services.transcription.schemas import SOURCE_ASSEMBLYAI, TranscriptResult
from app.services.transcription.service import transcription_service
from app.services.transcription.youtube import extract_youtube_id
from app.services.web_scraper.school_scraper_service import SchoolScraperService
from scripts.school_data.scrape_media_inventory import (
    AV_MEDIA_TYPES,
    normalise_record,
)

OUTPUT_DIR = Path(__file__).parent / "output"
DEFAULT_INPUT = OUTPUT_DIR / "finalised_20_disticts.json"
DEFAULT_OUTPUT_DIR = OUTPUT_DIR / "transcripts"

# Published AssemblyAI rate, for the estimate only.
USD_PER_AUDIO_HOUR = 0.23


def slugify(value: str, max_length: int = 48) -> str:
    """Filesystem-safe, human-readable slug."""
    cleaned = re.sub(r"[^A-Za-z0-9]+", "-", str(value or "")).strip("-").lower()
    return (cleaned[:max_length].rstrip("-")) or "untitled"


def district_dir_name(record: dict[str, Any]) -> str:
    """`03480000__worcester` — sorts by org_code, readable at a glance."""
    return f"{record.get('org_code') or 'no-code'}__{slugify(record.get('name'), 40)}"


def item_basename(index: int, item: dict[str, Any]) -> str:
    """`004__youtube__n_SOB-VqQh0__october-board-meeting`.

    Ordinal first so files sort in discovery order; media_type next so the free
    and paid items are visually separable; then a stable identifier (the video
    ID, or the filename stem) followed by the human label.
    """
    media_type = str(item.get("media_type") or "media")

    video_id = extract_youtube_id(item.get("url") or "")
    if video_id:
        identifier = video_id
    else:
        stem = Path(str(item.get("url") or "").split("?")[0]).stem
        identifier = slugify(stem, 32)

    label = slugify(item.get("name") or "", 40)
    parts = [f"{index:03d}", media_type, identifier]
    if label and label != identifier.lower():
        parts.append(label)
    return "__".join(parts)


async def collect_av_items(
    records: list[dict[str, Any]],
    crawl_depth: int,
    concurrency: int,
) -> list[dict[str, Any]]:
    """Scrape every district and return only the audio/video/YouTube items."""
    semaphore = asyncio.Semaphore(concurrency)
    out: list[dict[str, Any]] = []
    lock = asyncio.Lock()

    async def one(record: dict[str, Any]) -> None:
        url = record.get("correct_url")
        if not url:
            return
        try:
            async with semaphore:
                async with SchoolScraperService() as scraper:
                    scraped = await scraper.scrape_media_files(
                        page_url=url, crawl_depth=crawl_depth
                    )
        except Exception as exc:  # noqa: BLE001 — one bad site must not stop the rest
            print(f"  ! scrape failed {record.get('name')}: "
                  f"{type(exc).__name__}: {exc}")
            return

        av = [
            m
            for m in scraped.get("media_files", [])
            if m.get("media_type") in AV_MEDIA_TYPES
        ]
        async with lock:
            for item in av:
                out.append({"district": record, "item": item})
            print(f"  · {str(record.get('name'))[:34]:<34} a/v items: {len(av)}")

    await asyncio.gather(*(one(r) for r in records))
    return out


def write_transcript_files(
    base: Path,
    basename: str,
    result: TranscriptResult,
    district: dict[str, Any],
    item: dict[str, Any],
) -> dict[str, str]:
    """Write the JSON envelope plus a readable .txt beside it."""
    base.mkdir(parents=True, exist_ok=True)

    envelope = result.to_envelope()
    # Provenance, so a transcript file is self-describing months from now.
    envelope["_source"] = {
        "school_name": district.get("name"),
        "org_code": district.get("org_code"),
        "website": district.get("website"),
        "archive_url": district.get("correct_url"),
        "media_url": item.get("url"),
        "media_type": item.get("media_type"),
        "media_name": item.get("name"),
        "source_page_url": item.get("source_page_url"),
        "generated_at": datetime.now(UTC).isoformat(),
    }

    json_path = base / f"{basename}.json"
    with json_path.open("w", encoding="utf-8") as fh:
        json.dump(envelope, fh, indent=2, ensure_ascii=False)
        fh.write("\n")

    header = [
        f"School      : {district.get('name')}  ({district.get('org_code')})",
        f"Media       : {item.get('name') or item.get('url')}",
        f"Media URL   : {item.get('url')}",
        f"Archive URL : {district.get('correct_url')}",
        f"Source      : {result.source}"
        + (f" ({result.caption_kind})" if result.caption_kind else ""),
        f"Model       : {result.speech_model or 'n/a'}",
        f"Duration    : {result.duration_seconds or '?'} s",
        f"Segments    : {len(result.segments)}",
        f"Speakers    : {', '.join(result.speakers) or 'none (captions carry no speakers)'}",
        "=" * 72,
        "",
    ]
    # The provenance header above is prepended for human inspection only. The
    # `.txt` the pipeline actually consumes is written by the ingest tasks, not
    # here — this script dumps local copies for eyeballing a scrape.
    txt_path = base / f"{basename}.txt"
    txt_path.write_text(
        "\n".join(header) + result.to_text_document(), encoding="utf-8"
    )

    return {"json": str(json_path), "txt": str(txt_path)}


async def transcribe_one(
    entry: dict[str, Any],
    index: int,
    output_dir: Path,
    resume: bool,
) -> dict[str, Any]:
    """Transcribe a single item via the project service. Never raises."""
    district, item = entry["district"], entry["item"]
    base = output_dir / district_dir_name(district)
    basename = item_basename(index, item)

    record: dict[str, Any] = {
        "school_name": district.get("name"),
        "org_code": district.get("org_code"),
        "media_type": item.get("media_type"),
        "media_url": item.get("url"),
        "media_name": item.get("name"),
        "file": f"{district_dir_name(district)}/{basename}",
        "status": "pending",
        "source": None,
        "caption_kind": None,
        "speech_model": None,
        "duration_seconds": None,
        "segments": 0,
        "paid": False,
        "estimated_usd": 0.0,
        "elapsed_seconds": 0.0,
        "error": None,
    }

    if resume and (base / f"{basename}.json").exists():
        record["status"] = "skipped_existing"
        return record

    workdir = Path(tempfile.mkdtemp(dir=settings.SCHOOL_SCRAPER_MEDIA_TEMP_DIR))
    started = time.monotonic()
    try:
        url = str(item.get("url"))
        if item.get("media_type") == "youtube":
            result = await transcription_service.transcribe_youtube(
                url, workdir=workdir
            )
        else:
            result = await transcription_service.transcribe_media_url(
                url, workdir=workdir
            )

        if result.is_empty:
            record["status"] = "no_transcript"
            record["error"] = "transcript was empty"
            return record

        paths = write_transcript_files(base, basename, result, district, item)
        record.update(
            {
                "status": "ok",
                "source": result.source,
                "caption_kind": result.caption_kind,
                "speech_model": result.speech_model,
                "duration_seconds": result.duration_seconds,
                "segments": len(result.segments),
                "paid": result.source == SOURCE_ASSEMBLYAI,
                "json_path": paths["json"],
                "txt_path": paths["txt"],
            }
        )
        if record["paid"] and result.duration_seconds:
            record["estimated_usd"] = round(
                result.duration_seconds / 3600 * USD_PER_AUDIO_HOUR, 4
            )
    except TerminalTranscriptionError as exc:
        # Deterministic: the same status the Celery task would record.
        record["status"] = exc.status
        record["error"] = str(exc)
    except Exception as exc:  # noqa: BLE001
        record["status"] = "failed"
        record["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        record["elapsed_seconds"] = round(time.monotonic() - started, 1)
        shutil.rmtree(workdir, ignore_errors=True)

    return record


async def run(args: argparse.Namespace) -> int:
    input_path = Path(args.input)
    output_dir = Path(args.output_dir)

    if not input_path.exists():
        print(f"ERROR: input not found: {input_path}", file=sys.stderr)
        return 1

    with input_path.open("r", encoding="utf-8") as fh:
        records = [normalise_record(r) for r in json.load(fh)]

    if args.org_code:
        records = [r for r in records if r.get("org_code") == args.org_code]

    print("=" * 72)
    print("Just-EdTech Transcript Generation (uses app.services.transcription)")
    print(f"  input      : {input_path}")
    print(f"  output dir : {output_dir}")
    print(f"  districts  : {len(records)}")
    print(f"  audio mode : {settings.TRANSCRIPTION_AUDIO_MODE}")
    print(f"  models     : {settings.ASSEMBLYAI_SPEECH_MODELS}")
    print(f"  speakers   : {settings.ASSEMBLYAI_SPEAKER_LABELS}")
    print(f"  duration cap: {settings.SCHOOL_SCRAPER_MEDIA_MAX_DURATION_MINUTES} min")
    print("=" * 72)
    print("Scraping districts for audio/video items...")

    entries = await collect_av_items(records, args.crawl_depth, args.concurrency)

    if args.youtube_only:
        entries = [e for e in entries if e["item"].get("media_type") == "youtube"]
    if args.limit_items:
        entries = entries[: args.limit_items]

    by_type = Counter(e["item"].get("media_type") for e in entries)
    paid_candidates = sum(v for k, v in by_type.items() if k != "youtube")

    print()
    print(f"Found {len(entries)} audio/video items: {dict(by_type)}")
    print(f"  free unless captions are missing : {by_type.get('youtube', 0)} YouTube")
    print(f"  certainly paid                   : {paid_candidates} direct audio/video")
    print()

    if args.dry_run:
        for entry in entries:
            item, district = entry["item"], entry["district"]
            marker = "FREE?" if item.get("media_type") == "youtube" else "PAID "
            print(f"  [{marker}] {str(district.get('name'))[:26]:<26} "
                  f"{str(item.get('media_type')):<8} {str(item.get('url'))[:70]}")
        print()
        print("DRY RUN — nothing transcribed, nothing spent.")
        print("Re-run without --dry-run to generate transcripts.")
        return 0

    if paid_candidates and not settings.ASSEMBLYAI_API_KEY:
        print(
            f"ERROR: {paid_candidates} items need AssemblyAI but "
            "ASSEMBLYAI_API_KEY is not set.\n"
            "       Use --youtube-only to run the free path instead.",
            file=sys.stderr,
        )
        return 1

    output_dir.mkdir(parents=True, exist_ok=True)
    Path(settings.SCHOOL_SCRAPER_MEDIA_TEMP_DIR).mkdir(parents=True, exist_ok=True)

    semaphore = asyncio.Semaphore(args.concurrency)
    results: list[dict[str, Any]] = []
    lock = asyncio.Lock()
    done = 0
    total = len(entries)

    async def worker(index: int, entry: dict[str, Any]) -> None:
        nonlocal done
        async with semaphore:
            record = await transcribe_one(entry, index, output_dir, args.resume)
        async with lock:
            done += 1
            results.append(record)
            flag = {"ok": "✓", "skipped_existing": "-"}.get(record["status"], "✗")
            cost = f"${record['estimated_usd']}" if record["paid"] else "$0"
            print(
                f"[{done:>3}/{total}] {flag} {str(record['school_name'])[:24]:<24} "
                f"{str(record['media_type']):<8} "
                f"src={str(record['source'] or record['status'])[:17]:<17} "
                f"segs={record['segments']:<5} {cost:<8} "
                f"{record['elapsed_seconds']}s"
                + (f"  {record['error']}" if record.get("error") else "")
            )
            _write_manifest(output_dir, input_path, results)

    await asyncio.gather(*(worker(i, e) for i, e in enumerate(entries, start=1)))

    results.sort(key=lambda r: r["file"])
    _write_manifest(output_dir, input_path, results)

    statuses = Counter(r["status"] for r in results)
    spend = round(sum(r["estimated_usd"] for r in results), 2)
    free_ok = sum(1 for r in results if r["status"] == "ok" and not r["paid"])

    print("=" * 72)
    print(f"  transcripts written : {statuses.get('ok', 0)} / {total}")
    print(f"  free (captions)     : {free_ok}")
    print(f"  paid (AssemblyAI)   : {sum(1 for r in results if r['paid'])}")
    print(f"  statuses            : {dict(statuses)}")
    print(f"  ESTIMATED SPEND     : ${spend}")
    print(f"  output              : {output_dir}")
    print("=" * 72)
    return 0


def _write_manifest(
    output_dir: Path, input_path: Path, results: list[dict[str, Any]]
) -> None:
    """Index of every transcript, so files map back to their source media."""
    statuses = Counter(r["status"] for r in results)
    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "input_file": input_path.name,
        "transcription": {
            "audio_mode": settings.TRANSCRIPTION_AUDIO_MODE,
            "speech_models": settings.ASSEMBLYAI_SPEECH_MODELS,
            "speaker_labels": settings.ASSEMBLYAI_SPEAKER_LABELS,
            "duration_cap_minutes": settings.SCHOOL_SCRAPER_MEDIA_MAX_DURATION_MINUTES,
        },
        "summary": {
            "total": len(results),
            "by_status": dict(statuses),
            "free": sum(1 for r in results if r["status"] == "ok" and not r["paid"]),
            "paid": sum(1 for r in results if r["paid"]),
            "estimated_usd": round(sum(r["estimated_usd"] for r in results), 2),
        },
        "transcripts": results,
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / "manifest.json"
    tmp = path.with_suffix(".json.tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    tmp.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Generate transcripts for district audio/video/YouTube."
    )
    parser.add_argument("--input", default=str(DEFAULT_INPUT))
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    parser.add_argument("--crawl-depth", type=int, default=1)
    parser.add_argument("--concurrency", type=int, default=3)
    parser.add_argument("--org-code", default=None)
    parser.add_argument(
        "--limit-items", type=int, default=None, help="Cap total items transcribed."
    )
    parser.add_argument(
        "--youtube-only",
        action="store_true",
        help="Free path only — skip direct audio/video entirely.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="List what would be transcribed and stop. Spends nothing.",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Skip items whose transcript JSON already exists.",
    )
    args = parser.parse_args()

    args.crawl_depth = max(0, min(args.crawl_depth, 3))
    args.concurrency = max(1, args.concurrency)

    return asyncio.run(run(args))


if __name__ == "__main__":
    sys.exit(main())
