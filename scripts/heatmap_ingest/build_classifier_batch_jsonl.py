"""
Build OpenAI Batch API JSONL for the heatmap chunk classifier.

Official OpenAI Batch API flow (https://developers.openai.com/api/docs/guides/batch):

  1. Build a .jsonl file — one request per line, each with a unique custom_id,
     method=POST, url=/v1/chat/completions, body=Chat Completions params.
  2. Upload it via the Files API with purpose="batch".
  3. Create a batch: batches.create(input_file_id, endpoint, completion_window="24h").
  4. Poll batches.retrieve(batch_id) until terminal (completed/failed/expired/cancelled).
  5. Download output_file_id (successes) AND error_file_id (failures) via files.content.
     Results are NOT in input order — join on custom_id.

Per-request failures land in error_file_id, not output_file_id. A "completed"
batch with request_counts.failed > 0 is a partial success, not a clean pass.

The apply path (writing classification results back to Qdrant) lives in
BatchClassifier.apply_batch_results, which joins custom_id →
pending_classifications.id → qdrant_point_id. A JSONL whose custom_id is a
raw Qdrant point UUID CANNOT be applied by that code path. To write vectors,
enqueue pending rows first (see the enqueue subcommand) so custom_id becomes
the real pending id, then use BatchClassifier.submit_pending_batch +
apply_batch_results.

Subcommands:

  export-qdrant  Scroll a tenant's local Qdrant chunks collection and build
                 an input.jsonl using the current prompt.py. Primarily for
                 small pilots (use --limit). Does NOT touch pending_classifications.
  rebuild        Refresh SYSTEM_PROMPT + response_format on an existing JSONL,
                 keeping custom_id / chunk_text / DOC context per line.
  submit         Upload + create an OpenAI Batch (official sequence).
  wait           Poll a submitted batch until terminal, download output.jsonl
                 AND error.jsonl (when error_file_id is present).
  status         One-shot retrieve: print status, request_counts, file ids.

Usage:

  poetry run python -m scripts.heatmap_ingest.build_classifier_batch_jsonl \\
    export-qdrant --tenant-id 2 --limit 100 --out runs/tenant2_pilot/input.jsonl

  poetry run python -m scripts.heatmap_ingest.build_classifier_batch_jsonl \\
    submit --input runs/tenant2_pilot/input.jsonl --meta runs/tenant2_pilot/batch_meta.json

  poetry run python -m scripts.heatmap_ingest.build_classifier_batch_jsonl \\
    wait --meta runs/tenant2_pilot/batch_meta.json \\
    --out runs/tenant2_pilot/output.jsonl \\
    --error-out runs/tenant2_pilot/error.jsonl

Environment: requires LLM_API_PROVIDER=openai and a valid OPENAI_API_KEY.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any

from qdrant_client import QdrantClient
from qdrant_client.http import models as qdrant_models
from sqlalchemy import select

from app.core.config import settings
from app.db.connector import AsyncSessionLocal
from app.models.documents import Document
from app.services.heatmap_ingest.prompt import (
    SYSTEM_PROMPT,
    build_batch_request_line,
    build_response_format_schema,
    serialize_batch_line,
)
from app.services.llm.client import get_llm_api_key, normalize_model_name, uses_openrouter

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("build_classifier_batch_jsonl")

SCROLL_BATCH = 1024
# OpenAI Batch hard caps: 50,000 requests and 200 MB per input file.
# Our ~24 KB system prompt yields ~35 KB/line, so the file-size cap bites
# at ~5,700 lines — well below 50,000. Keep a safety margin under 200 MB.
DEFAULT_MAX_BYTES = 180 * 1024 * 1024
# Stable prompt_cache_key so requests sharing the static SYSTEM_PROMPT prefix
# route to the same cache. Batch fans work across workers, so hits are
# unreliable — do not budget cost as if cache hits (see SKILL.md pitfall).
DEFAULT_PROMPT_CACHE_KEY = "heatmap_chunk_classifier_v1"

TERMINAL_STATUSES = {"completed", "failed", "expired", "cancelled"}
BATCH_FAILURE_STATUSES = {"failed", "expired", "cancelled"}


def _refuse_openrouter() -> None:
    """OpenRouter does not expose the OpenAI Batch API. Refuse early."""
    if uses_openrouter():
        raise SystemExit(
            "LLM_API_PROVIDER=openrouter — OpenRouter has no Batch API. "
            "Set LLM_API_PROVIDER=openai and OPENAI_API_KEY to use this script."
        )


def _openai_model() -> str:
    """Return the bare OpenAI model name (provider prefix stripped)."""
    model = getattr(
        settings, "HEATMAP_INGEST_CHUNK_CLASSIFIER_MODEL", "openai/gpt-4o-mini"
    )
    return normalize_model_name(model)


def _get_qdrant_client() -> QdrantClient:
    return QdrantClient(url=settings.QDRANT_URL, check_compatibility=False)


def _collection_name(tenant_id: int) -> str:
    return f"{settings.QDRANT_COLLECTION_PREFIX}_{tenant_id}_documents"


async def _state_for_doc_id(db, document_id: int | None) -> str | None:
    """Look up the 2-letter state for a document id (None if missing)."""
    if document_id is None:
        return None
    doc = await db.get(Document, document_id)
    if doc is None:
        return None
    return doc.state


def _add_prompt_cache_key(body: dict[str, Any], key: str | None) -> dict[str, Any]:
    """Attach a stable prompt_cache_key to a Chat Completions request body.

    Prompt caching is automatic for gpt-4o-mini when the prefix is long enough;
    a stable key helps route requests that share the static SYSTEM_PROMPT
    prefix to the same cache. Batch API has no separate "enable caching" flag,
    and prior runs measured cached_tokens=0 — this is best-effort, not a cost
    assumption.
    """
    if key:
        body = dict(body)
        body["prompt_cache_key"] = key
    return body


# ---------------------------------------------------------------------------
# export-qdrant
# ---------------------------------------------------------------------------


async def cmd_export_qdrant(args: argparse.Namespace) -> None:
    """Scroll a tenant's Qdrant chunks collection and write a batch input JSONL.

    The exported JSONL is for inspection / pilot submission only — custom_id is
    `qdrant:{point_id}`, which BatchClassifier.apply_batch_results CANNOT join
    (it joins on str(pending_classifications.id)). To apply results back to
    Qdrant, use the enqueue + submit-pending + apply path (Phase 2 in the plan),
    not this export.
    """
    _refuse_openrouter()
    model = _openai_model()
    client = _get_qdrant_client()
    collection_name = _collection_name(args.tenant_id)

    # Confirm the collection exists before scrolling.
    try:
        info = await asyncio.to_thread(client.get_collection, collection_name)
        logger.info(
            "Collection %s: %s points", collection_name, info.points_count
        )
    except Exception as exc:
        raise SystemExit(f"Could not open collection '{collection_name}': {exc}")

    written = 0
    skipped = 0
    bytes_written = 0
    seen_custom_ids: set[str] = set()
    state_cache: dict[int, str | None] = {}

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path = (
        out_path.with_suffix(".manifest.jsonl") if args.manifest else None
    )

    with open(out_path, "w", encoding="utf-8") as out:
        manifest_fp = (
            open(manifest_path, "w", encoding="utf-8")
            if manifest_path
            else None
        )
        try:
            offset = None
            async with AsyncSessionLocal() as db:
                while True:
                    batch, offset = await asyncio.to_thread(
                        client.scroll,
                        collection_name=collection_name,
                        limit=SCROLL_BATCH,
                        offset=offset,
                        with_payload=qdrant_models.PayloadSelectorInclude(
                            include=[
                                "text",
                                "document_id",
                                "chunk_index",
                                "entity_type",
                                "meeting_date",
                                "classified",
                            ]
                        ),
                        with_vectors=False,
                    )
                    for point in batch:
                        payload = point.payload or {}
                        chunk_text = payload.get("text")
                        if not chunk_text:
                            skipped += 1
                            continue
                        if args.only_classified and not payload.get("classified"):
                            skipped += 1
                            continue
                        if args.skip_classified and payload.get("classified"):
                            skipped += 1
                            continue

                        document_id = payload.get("document_id")
                        if document_id is not None:
                            document_id = int(document_id)
                        if document_id not in state_cache:
                            state_cache[document_id] = await _state_for_doc_id(
                                db, document_id
                            )
                        state = state_cache[document_id]

                        meeting_date = payload.get("meeting_date")
                        if isinstance(meeting_date, str):
                            meeting_date_iso = meeting_date
                        elif meeting_date is not None:
                            meeting_date_iso = str(meeting_date)
                        else:
                            meeting_date_iso = None

                        custom_id = f"qdrant:{point.id}"
                        if custom_id in seen_custom_ids:
                            logger.warning(
                                "Duplicate custom_id %s — skipping", custom_id
                            )
                            skipped += 1
                            continue
                        seen_custom_ids.add(custom_id)

                        request = build_batch_request_line(
                            custom_id=custom_id,
                            chunk_text=chunk_text,
                            entity_type=payload.get("entity_type"),
                            meeting_date=meeting_date_iso,
                            state=state,
                            model=model,
                        )
                        request["body"] = _add_prompt_cache_key(
                            request["body"], args.prompt_cache_key
                        )
                        line = serialize_batch_line(request)
                        out.write(line + "\n")
                        bytes_written += len(line.encode("utf-8")) + 1

                        if manifest_fp is not None:
                            manifest_fp.write(
                                json.dumps(
                                    {
                                        "custom_id": custom_id,
                                        "point_id": str(point.id),
                                        "document_id": document_id,
                                        "chunk_index": payload.get("chunk_index"),
                                        "state": state,
                                        "classified": payload.get("classified"),
                                    },
                                    ensure_ascii=False,
                                )
                                + "\n"
                            )

                        written += 1
                        if written >= args.limit:
                            logger.info(
                                "Reached --limit %d; stopping scroll", args.limit
                            )
                            break

                    if written >= args.limit or offset is None:
                        break
        finally:
            if manifest_fp is not None:
                manifest_fp.close()

    logger.info(
        "Wrote %d lines (%.1f MB) to %s; skipped %d",
        written,
        bytes_written / 1024 / 1024,
        out_path,
        skipped,
    )
    if manifest_path is not None:
        logger.info("Wrote manifest to %s", manifest_path)
    if bytes_written > DEFAULT_MAX_BYTES:
        logger.warning(
            "Output is %.1f MB — over the 200 MB Batch file cap. Split it "
            "before submitting (see --max-bytes on a future split subcommand).",
            bytes_written / 1024 / 1024,
        )


# ---------------------------------------------------------------------------
# export-pending
# ---------------------------------------------------------------------------


async def cmd_export_pending(args: argparse.Namespace) -> None:
    """Build a batch input JSONL from pending_classifications rows.

    This is the apply-ready path: custom_id = str(pending.id), so the output
    can be applied back to Qdrant via BatchClassifier.apply_batch_results
    (which joins custom_id -> pending_classifications.id -> qdrant_point_id).

    Pulls rows by status (default 'failed' — the ones that need re-classifying),
    limited to --limit. Does NOT flip status; safe to preview.
    """
    _refuse_openrouter()
    model = _openai_model()
    from app.models.pending_classification import PendingClassification

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    async with AsyncSessionLocal() as db:
        rows = (
            (
                await db.execute(
                    select(PendingClassification)
                    .where(PendingClassification.status == args.status)
                    .order_by(PendingClassification.id)
                    .limit(args.limit)
                )
            )
            .scalars()
            .all()
        )
        if not rows:
            logger.info(
                "No pending_classifications with status=%r to export.", args.status
            )
            return

        # Resolve state per document (cached) so the state vocabulary pack (A3b)
        # is injected into each chunk's prompt.
        doc_state_cache: dict[int, str | None] = {}
        lines: list[str] = []
        manifest_lines: list[str] = []
        for row in rows:
            if row.document_id not in doc_state_cache:
                doc_state_cache[row.document_id] = await _state_for_doc_id(
                    db, row.document_id
                )
            state = doc_state_cache[row.document_id]
            meeting_date_iso = row.meeting_date.isoformat() if row.meeting_date else None
            custom_id = str(row.id)
            request = build_batch_request_line(
                custom_id=custom_id,
                chunk_text=row.chunk_text,
                entity_type=row.entity_type,
                meeting_date=meeting_date_iso,
                state=state,
                model=model,
            )
            request["body"] = _add_prompt_cache_key(request["body"], args.prompt_cache_key)
            lines.append(serialize_batch_line(request))
            manifest_lines.append(
                json.dumps(
                    {
                        "custom_id": custom_id,
                        "pending_id": row.id,
                        "document_id": row.document_id,
                        "qdrant_point_id": str(row.qdrant_point_id),
                        "chunk_index": row.chunk_index,
                        "state": state,
                        "status": row.status,
                    },
                    ensure_ascii=False,
                )
            )

    bytes_written = sum(len(l.encode("utf-8")) + 1 for l in lines)
    with open(out_path, "w", encoding="utf-8") as out:
        out.write("\n".join(lines) + "\n")
    logger.info(
        "Wrote %d lines (%.1f KB) to %s (status=%s, custom_id=pending.id)",
        len(lines),
        bytes_written / 1024,
        out_path,
        args.status,
    )
    if args.manifest:
        manifest_path = out_path.with_suffix(".manifest.jsonl")
        with open(manifest_path, "w", encoding="utf-8") as m:
            m.write("\n".join(manifest_lines) + "\n")
        logger.info("Wrote manifest to %s", manifest_path)


# ---------------------------------------------------------------------------
# rebuild
# ---------------------------------------------------------------------------


def cmd_rebuild(args: argparse.Namespace) -> None:
    """Refresh SYSTEM_PROMPT + response_format on an existing JSONL.

    Keeps custom_id / chunk_text / DOC context per line; only the prompt and
    schema are refreshed from the current prompt.py. Mirrors the eval runner's
    `build` command.
    """
    response_format = build_response_format_schema()
    n = 0
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(args.source, encoding="utf-8") as src, open(
        out_path, "w", encoding="utf-8"
    ) as out:
        for line in src:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            for msg in row.get("body", {}).get("messages", []):
                if msg.get("role") == "system":
                    msg["content"] = SYSTEM_PROMPT
            row["body"]["response_format"] = response_format
            out.write(json.dumps(row, ensure_ascii=False) + "\n")
            n += 1
    logger.info(
        "Wrote %d requests to %s (current prompt.py, same chunks as %s)",
        n,
        out_path,
        args.source,
    )


# ---------------------------------------------------------------------------
# submit
# ---------------------------------------------------------------------------


def cmd_submit(args: argparse.Namespace) -> None:
    """Upload + create an OpenAI Batch (official sequence).

    Per the OpenAI Batch guide: upload the .jsonl via files.create with
    purpose="batch", then batches.create with endpoint=/v1/chat/completions
    and completion_window="24h". The creation response only returns the
    batch id and status="validating" — output_file_id / error_file_id are
    not populated until the batch reaches a terminal state, so you must poll
    with `wait` or `status`.
    """
    _refuse_openrouter()
    get_llm_api_key()
    from openai import OpenAI

    client = OpenAI()
    input_path = Path(args.input)
    if not input_path.is_file():
        raise SystemExit(f"Input file not found: {input_path}")

    size_mb = input_path.stat().st_size / 1024 / 1024
    logger.info("Uploading %s (%.1f MB)", input_path, size_mb)
    if size_mb > 200:
        raise SystemExit(
            f"Input file is {size_mb:.1f} MB — over the 200 MB Batch cap. "
            "Split it before submitting."
        )

    with open(input_path, "rb") as f:
        uploaded = client.files.create(file=f, purpose="batch")
    logger.info("Uploaded file id=%s", uploaded.id)

    batch = client.batches.create(
        input_file_id=uploaded.id,
        endpoint="/v1/chat/completions",
        completion_window="24h",
        metadata={
            "purpose": "heatmap_chunk_classification",
            "script": "build_classifier_batch_jsonl",
        },
    )
    logger.info(
        "Created batch id=%s status=%s", batch.id, batch.status
    )

    meta = {
        "batch_id": batch.id,
        "input_file_id": uploaded.id,
        "input_path": str(input_path),
        "created_status": batch.status,
    }
    meta_path = Path(args.meta)
    meta_path.parent.mkdir(parents=True, exist_ok=True)
    meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")
    logger.info("Wrote %s", meta_path)


# ---------------------------------------------------------------------------
# wait
# ---------------------------------------------------------------------------


def _format_counts(batch) -> str:
    counts = getattr(batch, "request_counts", None)
    if counts is None:
        return "(no request_counts yet)"
    return (
        f"completed={counts.completed}/{counts.total} "
        f"failed={counts.failed}"
    )


def _download_file(client, file_id: str, dest: Path) -> None:
    """Download a Batch output/error file via the Files API."""
    content = client.files.content(file_id)
    dest.parent.mkdir(parents=True, exist_ok=True)
    with open(dest, "wb") as f:
        f.write(content.read())
    logger.info("Downloaded file %s -> %s", file_id, dest)


def cmd_wait(args: argparse.Namespace) -> None:
    """Poll a submitted batch until terminal, then download output + error files.

    Prints status + request_counts on every poll. On terminal:
      - failed/expired/cancelled -> exit non-zero, download error_file_id if present
      - completed -> download output_file_id; if request_counts.failed > 0 also
        download error_file_id and print the failed custom_ids
    """
    _refuse_openrouter()
    get_llm_api_key()
    from openai import OpenAI

    client = OpenAI()
    meta = json.loads(Path(args.meta).read_text(encoding="utf-8"))
    batch_id = meta["batch_id"]

    poll_s = args.poll_seconds
    while True:
        batch = client.batches.retrieve(batch_id)
        counts_str = _format_counts(batch)
        logger.info(
            "batch %s status=%s %s",
            batch_id,
            batch.status,
            counts_str,
        )
        if batch.status in TERMINAL_STATUSES:
            break
        time.sleep(poll_s)

    out_path = Path(args.out)
    error_out_path = Path(args.error_out) if args.error_out else None

    if batch.status in BATCH_FAILURE_STATUSES:
        # Whole-batch failure. Surface the validation/endpoint errors and
        # any error_file_id before exiting non-zero.
        errs = getattr(batch, "errors", None)
        if errs is not None and getattr(errs, "data", None):
            logger.error("Batch %s errors:", batch_id)
            for e in errs.data:
                logger.error(
                    "  code=%s message=%s line=%s param=%s",
                    getattr(e, "code", ""),
                    getattr(e, "message", ""),
                    getattr(e, "line", ""),
                    getattr(e, "param", ""),
                )
        else:
            logger.error("Batch %s ended %s (no error details)", batch_id, batch.status)
        error_file_id = getattr(batch, "error_file_id", None)
        if error_file_id and error_out_path:
            _download_file(client, error_file_id, error_out_path)
        # Also download any partial output if present (expired batches can have one).
        output_file_id = getattr(batch, "output_file_id", None)
        if output_file_id:
            _download_file(client, output_file_id, out_path)
        raise SystemExit(
            f"Batch {batch_id} ended {batch.status} — see errors above"
        )

    # completed
    output_file_id = getattr(batch, "output_file_id", None)
    if not output_file_id:
        raise SystemExit(
            f"Batch {batch_id} completed but has no output_file_id"
        )
    _download_file(client, output_file_id, out_path)

    counts = getattr(batch, "request_counts", None)
    failed_count = getattr(counts, "failed", 0) if counts else 0

    error_file_id = getattr(batch, "error_file_id", None)
    if error_file_id:
        if error_out_path is None:
            # Default the error file next to the output file so failures are
            # never silently dropped.
            error_out_path = out_path.with_suffix(".error.jsonl")
        _download_file(client, error_file_id, error_out_path)
        _print_failed_custom_ids(error_out_path, out_path)
    elif failed_count:
        logger.warning(
            "request_counts.failed=%s but no error_file_id — failures may be "
            "inlined in the output file. Inspect %s for lines with an 'error' field.",
            failed_count,
            out_path,
        )
        _print_failed_custom_ids(out_path, out_path)

    # Log cached_tokens if present (best-effort; Batch usually reports 0).
    _log_usage(batch)

    logger.info("FINAL STATUS: completed. Output: %s", out_path)
    if failed_count:
        logger.warning(
            "Partial success: %s request(s) failed. Treat as partial, not clean.",
            failed_count,
        )


def _print_failed_custom_ids(error_path: Path, output_path: Path) -> None:
    """Print failed custom_ids from an error file or inlined output errors."""
    seen = 0
    # Prefer the dedicated error file; fall back to inlined errors in output.
    for path in (error_path, output_path):
        if not path.exists():
            continue
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if row.get("error"):
                    cid = row.get("custom_id", "?")
                    err = row.get("error")
                    logger.warning("  failed custom_id=%s error=%s", cid, err)
                    seen += 1
        if seen:
            break
    if seen:
        logger.info("Printed %d failed request(s)", seen)


def _log_usage(batch) -> None:
    """Log cached_tokens from batch.usage if present (best-effort)."""
    usage = getattr(batch, "usage", None)
    if usage is None:
        return
    cached = (
        getattr(getattr(usage, "input_tokens_details", None), "cached_tokens", 0)
        or 0
    )
    input_tokens = getattr(usage, "input_tokens", 0)
    output_tokens = getattr(usage, "output_tokens", 0)
    logger.info(
        "batch.usage: input=%s output=%s cached_tokens=%s",
        input_tokens,
        output_tokens,
        cached,
    )
    if cached:
        logger.info(
            "Prompt caching fired on Batch (cached_tokens=%s) — unexpected but "
            "welcome; do not budget future runs on this.",
            cached,
        )
    else:
        logger.info(
            "cached_tokens=0 — consistent with Batch fanning work across "
            "workers; do not assume cache discounts on Batch."
        )


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------


def cmd_status(args: argparse.Namespace) -> None:
    """One-shot retrieve: print status, request_counts, file ids."""
    _refuse_openrouter()
    get_llm_api_key()
    from openai import OpenAI

    client = OpenAI()
    batch = client.batches.retrieve(args.batch_id)
    counts = getattr(batch, "request_counts", None)
    print(f"batch_id: {batch.id}")
    print(f"status:   {batch.status}")
    if counts:
        print(
            f"counts:   completed={counts.completed}/{counts.total} "
            f"failed={counts.failed}"
        )
    print(f"output_file_id: {getattr(batch, 'output_file_id', None)}")
    print(f"error_file_id:  {getattr(batch, 'error_file_id', None)}")
    usage = getattr(batch, "usage", None)
    if usage:
        cached = (
            getattr(getattr(usage, "input_tokens_details", None), "cached_tokens", 0)
            or 0
        )
        print(
            f"usage:    input={getattr(usage, 'input_tokens', 0)} "
            f"output={getattr(usage, 'output_tokens', 0)} cached={cached}"
        )
    if batch.status in BATCH_FAILURE_STATUSES:
        sys.exit(1)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = ap.add_subparsers(dest="cmd", required=True)

    e = sub.add_parser(
        "export-qdrant",
        help="Scroll a tenant's Qdrant chunks and build a batch input JSONL",
    )
    e.add_argument("--tenant-id", type=int, required=True)
    e.add_argument("--limit", type=int, default=200, help="max chunks to export")
    e.add_argument("--out", required=True, help="output .jsonl path")
    e.add_argument(
        "--only-classified",
        action="store_true",
        help="only export chunks already marked classified=true",
    )
    e.add_argument(
        "--skip-classified",
        action="store_true",
        help="skip chunks already marked classified=true",
    )
    e.add_argument(
        "--manifest",
        action="store_true",
        help="also write <out>.manifest.jsonl mapping custom_id -> point_id/doc",
    )
    e.add_argument(
        "--prompt-cache-key",
        default=DEFAULT_PROMPT_CACHE_KEY,
        help=f"prompt_cache_key for each request body (default: {DEFAULT_PROMPT_CACHE_KEY}); "
        "pass '' to omit",
    )
    e.set_defaults(func=cmd_export_qdrant)

    ep = sub.add_parser(
        "export-pending",
        help="Build JSONL from pending_classifications (apply-ready: custom_id=pending.id)",
    )
    ep.add_argument(
        "--status",
        default="failed",
        help="pending_classifications status to pull (default: failed)",
    )
    ep.add_argument("--limit", type=int, default=20, help="max rows to export")
    ep.add_argument("--out", required=True, help="output .jsonl path")
    ep.add_argument(
        "--manifest",
        action="store_true",
        help="also write <out>.manifest.jsonl",
    )
    ep.add_argument(
        "--prompt-cache-key",
        default=DEFAULT_PROMPT_CACHE_KEY,
        help=f"prompt_cache_key for each request body (default: {DEFAULT_PROMPT_CACHE_KEY})",
    )
    ep.set_defaults(func=cmd_export_pending)

    r = sub.add_parser(
        "rebuild",
        help="refresh SYSTEM_PROMPT + response_format on an existing JSONL",
    )
    r.add_argument("--source", required=True, help="existing batch input jsonl")
    r.add_argument("--out", required=True)
    r.set_defaults(func=cmd_rebuild)

    s = sub.add_parser("submit", help="upload + create an OpenAI Batch")
    s.add_argument("--input", required=True, help="input .jsonl path")
    s.add_argument("--meta", required=True, help="where to write batch metadata")
    s.set_defaults(func=cmd_submit)

    w = sub.add_parser(
        "wait",
        help="poll a submitted batch until terminal, download output + error files",
    )
    w.add_argument("--meta", required=True, help="batch_meta.json from `submit`")
    w.add_argument("--out", required=True, help="where to write output.jsonl")
    w.add_argument(
        "--error-out",
        default=None,
        help="where to write error.jsonl (default: alongside --out)",
    )
    w.add_argument("--poll-seconds", type=int, default=30)
    w.set_defaults(func=cmd_wait)

    st = sub.add_parser(
        "status", help="one-shot retrieve: status, counts, file ids"
    )
    st.add_argument("--batch-id", required=True)
    st.set_defaults(func=cmd_status)

    args = ap.parse_args()
    func = args.func
    if asyncio.iscoroutinefunction(func):
        try:
            asyncio.run(func(args))
        except KeyboardInterrupt:
            sys.exit(130)
    else:
        func(args)


if __name__ == "__main__":
    main()
