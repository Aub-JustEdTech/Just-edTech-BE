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

  count          Count classified vs. unclassified chunks for a tenant in
                 Qdrant, plus the pending_classifications breakdown (by
                 status) for that tenant's documents. Read-only.
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
from datetime import date
from pathlib import Path
from types import SimpleNamespace
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
# OpenAI Batch hard caps (universal, not tier-specific): 50,000 requests and
# 200 MB per input file. Our ~24 KB system prompt yields ~35 KB/line, so the
# file-size cap bites well below 50,000 requests. Keep a safety margin under
# 200 MB regardless of tier.
DEFAULT_MAX_BYTES = 180 * 1024 * 1024
HARD_MAX_REQUESTS_PER_BATCH = 50_000
HARD_MAX_FILE_BYTES = 200 * 1024 * 1024

# Tier-3 gpt-4o-mini Batch *queue* limit: 40,000,000 input tokens enqueued
# (waiting/in-progress) at once across all batches for that model. This is
# separate from the per-batch request/file caps above and from the
# tier's synchronous RPM/TPM limits (irrelevant to Batch). Since each
# submitted batch stays enqueued until it completes -- and completion can
# take up to the 24h completion window -- Tier 3 can realistically only
# have ONE batch this size in flight at a time; submits must be sequential
# (submit -> wait -> apply -> submit next), never fire-and-forget in a loop.
# We target 80% of the hard cap (not 100%) because per-request token counts
# below are an estimate (cl100k_base encode of the full JSONL line, which
# over-counts slightly vs. the model's actual o200k_base tokenizer) --
# the margin absorbs that estimation error plus any concurrent submission
# from the scheduled Celery tasks (submit_pending_batch_classification /
# poll_batch_classification) that also draw from this same queue limit.
TIER3_GPT4O_MINI_BATCH_QUEUE_LIMIT_TOKENS = 40_000_000
DEFAULT_SHARD_MAX_TOKENS = int(TIER3_GPT4O_MINI_BATCH_QUEUE_LIMIT_TOKENS * 0.8)

_TOKEN_ENCODER = None


def _estimate_tokens(text: str) -> int:
    """Best-effort input-token estimate for one JSONL line (system prompt +
    schema + chunk text combined). Uses tiktoken cl100k_base (same encoding
    the rest of this codebase uses for token-mode chunking -- see
    app/services/document_processing/chunker.py) as a stand-in for
    gpt-4o-mini's actual o200k_base tokenizer. Encoding the whole line
    (including JSON punctuation/keys) over-counts a little, which is the
    conservative direction we want for a queue-limit safety margin.
    Falls back to a chars/4 heuristic if tiktoken is unavailable.
    """
    global _TOKEN_ENCODER
    if _TOKEN_ENCODER is None:
        try:
            import tiktoken

            _TOKEN_ENCODER = tiktoken.get_encoding("cl100k_base")
        except Exception:  # noqa: BLE001
            _TOKEN_ENCODER = False
    if _TOKEN_ENCODER is False:
        return len(text) // 4
    return len(_TOKEN_ENCODER.encode(text, disallowed_special=()))
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
# count
# ---------------------------------------------------------------------------


async def cmd_count(args: argparse.Namespace) -> None:
    """Count classified vs. unclassified chunks for a tenant (read-only).

    Two independent counts are printed because they can legitimately
    disagree:

      - Qdrant `classified` payload field, scrolled per-point. This is the
        ground truth for "does this vector still need topic tags".
      - `pending_classifications` rows joined to this tenant's documents,
        grouped by status. This is what `submit_pending_batch` /
        `export-pending` actually operate on. A chunk can be
        classified=false in Qdrant with no pending row at all (e.g. the
        step6_accumulate_batch insert failed or predates this feature) --
        that gap is reported explicitly so it isn't silently missed.

    Also reports pending rows belonging to OTHER tenants, since
    submit_pending_batch()/BatchClassifier pull status='pending' globally
    (no tenant filter) -- if other tenants have pending rows, a manual
    submit for "tenant 4" will also drain theirs.
    """
    from sqlalchemy import func

    from app.models.documents import Document
    from app.models.pending_classification import PendingClassification

    client = _get_qdrant_client()
    collection_name = _collection_name(args.tenant_id)

    try:
        info = await asyncio.to_thread(client.get_collection, collection_name)
    except Exception as exc:
        raise SystemExit(f"Could not open collection '{collection_name}': {exc}")

    total_points = info.points_count
    classified_true = await asyncio.to_thread(
        client.count,
        collection_name=collection_name,
        count_filter=qdrant_models.Filter(
            must=[
                qdrant_models.FieldCondition(
                    key="classified", match=qdrant_models.MatchValue(value=True)
                )
            ]
        ),
        exact=True,
    )
    classified_false = await asyncio.to_thread(
        client.count,
        collection_name=collection_name,
        count_filter=qdrant_models.Filter(
            must=[
                qdrant_models.FieldCondition(
                    key="classified", match=qdrant_models.MatchValue(value=False)
                )
            ]
        ),
        exact=True,
    )
    classified_true_n = classified_true.count
    classified_false_n = classified_false.count
    no_field_n = total_points - classified_true_n - classified_false_n

    print("=" * 70)
    print(f"Qdrant collection: {collection_name}")
    print("=" * 70)
    print(f"  total chunks            : {total_points}")
    print(f"  classified = true       : {classified_true_n}")
    print(f"  classified = false      : {classified_false_n}  <-- needs classification")
    print(f"  classified field absent : {no_field_n}  (non-school_scraper docs, not gated)")

    async with AsyncSessionLocal() as db:
        tenant_rows = (
            await db.execute(
                select(PendingClassification.status, func.count())
                .join(Document, Document.id == PendingClassification.document_id)
                .where(Document.tenant_id == args.tenant_id)
                .group_by(PendingClassification.status)
            )
        ).all()
        other_tenant_pending = (
            await db.execute(
                select(func.count())
                .select_from(PendingClassification)
                .join(Document, Document.id == PendingClassification.document_id)
                .where(
                    Document.tenant_id != args.tenant_id,
                    PendingClassification.status == "pending",
                )
            )
        ).scalar_one()

    tenant_by_status = dict(tenant_rows)
    tenant_total = sum(tenant_by_status.values())

    print()
    print(f"pending_classifications for tenant {args.tenant_id} (by status):")
    for status in ("pending", "submitted", "applied", "failed", "dead_letter"):
        print(f"  {status:12s}: {tenant_by_status.get(status, 0)}")
    for status, n in tenant_by_status.items():
        if status not in ("pending", "submitted", "applied", "failed", "dead_letter"):
            print(f"  {status:12s}: {n}")
    print(f"  {'total':12s}: {tenant_total}")

    print()
    if other_tenant_pending:
        print(
            f"WARNING: {other_tenant_pending} pending_classifications row(s) belong to "
            f"OTHER tenants (status='pending'). submit_pending_batch has no tenant "
            f"filter -- a manual submit will pull those in too."
        )
    else:
        print("No pending_classifications rows for other tenants (status='pending').")

    gap = classified_false_n - tenant_by_status.get("pending", 0) - tenant_by_status.get("submitted", 0)
    print()
    print(
        f"Qdrant classified=false ({classified_false_n}) vs. tenant pending+submitted "
        f"({tenant_by_status.get('pending', 0) + tenant_by_status.get('submitted', 0)}): "
        f"gap = {gap}"
    )
    if gap > 0:
        print(
            "  A positive gap means some classified=false chunks have no active "
            "pending_classifications row -- they will NOT be picked up by "
            "submit_pending_batch/export-pending. Use `export-qdrant "
            "--skip-classified` to inspect/export them directly."
        )


# ---------------------------------------------------------------------------
# export-qdrant
# ---------------------------------------------------------------------------


class _ShardWriter:
    """Rotates output files (+ manifests) once a shard hits --shard-max-bytes.

    A single Qdrant Batch input file is capped well below the 200 MB/50k-request
    OpenAI limits by DEFAULT_MAX_BYTES (~5,000-7,000 lines), which itself sits
    just under the Tier-3 gpt-4o-mini batch *queue* limit (40,000,000 enqueued
    tokens) -- see module docstring. A large backlog (hundreds of thousands of
    chunks) therefore has to land in dozens of shard files, submitted and
    applied one at a time (Tier-3 can really only have ~one such shard in
    flight before hitting the queue cap).
    """

    def __init__(
        self,
        out_dir: Path,
        prefix: str,
        max_bytes: int,
        with_manifest: bool,
        max_tokens: int = DEFAULT_SHARD_MAX_TOKENS,
        max_requests: int = HARD_MAX_REQUESTS_PER_BATCH,
        exact_path: Path | None = None,
    ):
        """
        exact_path: when set (single-file/non-shard mode), the first (and,
        given max_bytes/max_tokens effectively unlimited in that mode, only)
        shard is written to exactly this path -- preserving `--out`'s
        historical exact-filename behavior instead of the numbered
        `{prefix}_0001.jsonl` naming --shard uses.
        """
        self.out_dir = out_dir
        self.prefix = prefix
        self.max_bytes = min(max_bytes, HARD_MAX_FILE_BYTES)
        self.max_tokens = max_tokens
        self.max_requests = max_requests
        self.with_manifest = with_manifest
        self.exact_path = exact_path
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self.shard_index = 0
        self.shard_bytes = 0
        self.shard_lines = 0
        self.shard_tokens = 0
        self.total_lines = 0
        self.total_tokens = 0
        self.paths: list[Path] = []
        self.shard_summaries: list[dict[str, int]] = []
        self._out_fp = None
        self._manifest_fp = None
        self._open_next_shard()

    def _shard_path(self, suffix: str) -> Path:
        if self.exact_path is not None and self.shard_index == 1:
            if suffix == ".jsonl":
                return self.exact_path
            return self.exact_path.with_suffix(suffix)
        return self.out_dir / f"{self.prefix}_{self.shard_index:04d}{suffix}"

    def _open_next_shard(self) -> None:
        self._record_shard_summary()
        self._close_current()
        self.shard_index += 1
        self.shard_bytes = 0
        self.shard_lines = 0
        self.shard_tokens = 0
        out_path = self._shard_path(".jsonl")
        self.paths.append(out_path)
        self._out_fp = open(out_path, "w", encoding="utf-8")
        if self.with_manifest:
            self._manifest_fp = open(
                self._shard_path(".manifest.jsonl"), "w", encoding="utf-8"
            )

    def _record_shard_summary(self) -> None:
        if self.shard_lines > 0:
            self.shard_summaries.append(
                {
                    "shard": self.shard_index,
                    "lines": self.shard_lines,
                    "bytes": self.shard_bytes,
                    "est_tokens": self.shard_tokens,
                }
            )

    def _close_current(self) -> None:
        if self._out_fp is not None:
            self._out_fp.close()
            self._out_fp = None
        if self._manifest_fp is not None:
            self._manifest_fp.close()
            self._manifest_fp = None

    def write(self, line: str, manifest_record: dict[str, Any] | None) -> None:
        line_bytes = len(line.encode("utf-8")) + 1
        line_tokens = _estimate_tokens(line)
        would_exceed = self.shard_lines > 0 and (
            self.shard_bytes + line_bytes > self.max_bytes
            or self.shard_tokens + line_tokens > self.max_tokens
            or self.shard_lines + 1 > self.max_requests
        )
        if would_exceed:
            self._open_next_shard()
        self._out_fp.write(line + "\n")
        self.shard_bytes += line_bytes
        self.shard_lines += 1
        self.shard_tokens += line_tokens
        self.total_lines += 1
        self.total_tokens += line_tokens
        if self._manifest_fp is not None and manifest_record is not None:
            self._manifest_fp.write(json.dumps(manifest_record, ensure_ascii=False) + "\n")

    def close(self) -> None:
        self._record_shard_summary()
        self._close_current()


async def cmd_export_qdrant(args: argparse.Namespace) -> None:
    """Scroll a tenant's Qdrant chunks collection and write batch input JSONL.

    The exported JSONL is for inspection / pilot submission by default --
    custom_id is `qdrant:{point_id}`, which BatchClassifier.apply_batch_results
    (the pending_classifications-based path) CANNOT join. Apply results from
    this export with the `apply-qdrant` subcommand, which parses the
    `qdrant:{point_id}` custom_id directly and writes straight to that point --
    no pending_classifications row required. Pass --manifest (default when
    --shard is set) so apply-qdrant has document_id/state/meeting_date/
    entity_type per point.

    Without --shard, writes a single file honoring --limit (small pilots).
    With --shard, ignores --limit and scrolls the *entire* collection,
    rotating to a new numbered file under --out-dir every --shard-max-bytes --
    use this to export a full backlog for sequential Tier-3-safe submission.
    """
    if args.shard and not args.out_dir:
        raise SystemExit("--out-dir is required with --shard")
    if not args.shard and not args.out:
        raise SystemExit("--out is required (or pass --shard --out-dir instead)")

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

    skipped = 0
    seen_custom_ids: set[str] = set()
    state_cache: dict[int, str | None] = {}

    if args.shard:
        writer = _ShardWriter(
            out_dir=Path(args.out_dir),
            prefix=args.prefix,
            max_bytes=args.shard_max_bytes,
            max_tokens=args.shard_max_tokens,
            max_requests=args.shard_max_requests,
            with_manifest=True,
        )
    else:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        writer = _ShardWriter(
            out_dir=out_path.parent,
            prefix=out_path.stem,
            max_bytes=10**18,  # effectively unlimited: single file
            max_tokens=10**18,
            max_requests=HARD_MAX_REQUESTS_PER_BATCH,
            with_manifest=args.manifest,
            exact_path=out_path,
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
                    manifest_record = {
                        "custom_id": custom_id,
                        "point_id": str(point.id),
                        "document_id": document_id,
                        "chunk_index": payload.get("chunk_index"),
                        "entity_type": payload.get("entity_type"),
                        "meeting_date": meeting_date_iso,
                        "state": state,
                        "classified": payload.get("classified"),
                    }
                    writer.write(line, manifest_record)

                    if not args.shard and writer.total_lines >= args.limit:
                        logger.info(
                            "Reached --limit %d; stopping scroll", args.limit
                        )
                        break

                if (not args.shard and writer.total_lines >= args.limit) or offset is None:
                    break
    finally:
        writer.close()

    logger.info(
        "Wrote %d lines (~%d est. input tokens) across %d file(s) under %s; skipped %d",
        writer.total_lines,
        writer.total_tokens,
        len(writer.paths),
        writer.out_dir,
        skipped,
    )
    for p, summary in zip(writer.paths, writer.shard_summaries):
        size_mb = summary["bytes"] / 1024 / 1024
        pct_of_cap = 100 * summary["est_tokens"] / TIER3_GPT4O_MINI_BATCH_QUEUE_LIMIT_TOKENS
        logger.info(
            "  %s: %d lines, %.1f MB, ~%d est. tokens (%.0f%% of Tier-3 40M queue cap)",
            p,
            summary["lines"],
            size_mb,
            summary["est_tokens"],
            pct_of_cap,
        )
    if args.shard:
        logger.info(
            "Submit these shards SEQUENTIALLY (submit -> wait -> apply-qdrant -> "
            "next shard). Tier 3's 40,000,000-token batch queue limit for "
            "gpt-4o-mini means having more than one of these in flight at once "
            "risks a queue-limit rejection."
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
    if size_mb > HARD_MAX_FILE_BYTES / 1024 / 1024:
        raise SystemExit(
            f"Input file is {size_mb:.1f} MB — over the "
            f"{HARD_MAX_FILE_BYTES // 1024 // 1024} MB Batch cap (universal, "
            "all tiers). Split it before submitting (see `export-qdrant --shard`)."
        )

    n_lines = 0
    est_tokens = 0
    with open(input_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            n_lines += 1
            est_tokens += _estimate_tokens(line)
    if n_lines > HARD_MAX_REQUESTS_PER_BATCH:
        raise SystemExit(
            f"Input file has {n_lines} requests — over the "
            f"{HARD_MAX_REQUESTS_PER_BATCH} Batch per-file request cap "
            "(universal, all tiers). Split it before submitting."
        )
    pct_of_cap = 100 * est_tokens / TIER3_GPT4O_MINI_BATCH_QUEUE_LIMIT_TOKENS
    logger.info(
        "%d requests, ~%d est. input tokens (%.0f%% of the Tier-3 gpt-4o-mini "
        "40,000,000-token batch queue limit)",
        n_lines,
        est_tokens,
        pct_of_cap,
    )
    if est_tokens > TIER3_GPT4O_MINI_BATCH_QUEUE_LIMIT_TOKENS and not args.force:
        raise SystemExit(
            f"Estimated ~{est_tokens} input tokens exceeds the Tier-3 "
            f"gpt-4o-mini batch queue limit ({TIER3_GPT4O_MINI_BATCH_QUEUE_LIMIT_TOKENS}) "
            "on its own -- OpenAI will likely reject this submission (or it "
            "will block any other batch for this model from being enqueued). "
            "Split it before submitting (see `export-qdrant --shard`), or pass "
            "--force if you are certain your org is on a higher tier."
        )
    if est_tokens > TIER3_GPT4O_MINI_BATCH_QUEUE_LIMIT_TOKENS:
        logger.warning(
            "--force set: submitting despite exceeding the Tier-3 queue limit estimate."
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
# apply-qdrant
# ---------------------------------------------------------------------------


async def cmd_apply_qdrant(args: argparse.Namespace) -> None:
    """Apply a completed batch's output straight to Qdrant, by point id.

    Counterpart to `export-qdrant`: that export's custom_id is
    `qdrant:{point_id}`, which BatchClassifier.apply_batch_results (the
    pending_classifications-based path) cannot join. This command parses
    that custom_id directly, so it needs no DB row per chunk -- it writes
    to Qdrant using --manifest (from export-qdrant --manifest, matching the
    exact shard) purely to recover document_id/state/meeting_date/entity_type
    for the same payload shape + heatmap_aggregate upsert that
    BatchClassifier.apply_batch_results produces.

    Reuses BatchClassifier._build_payload_metadata / _upsert_heatmap_aggregate
    (private, but pure/DB-only helpers with no OpenAI/S3 side effects) so the
    Qdrant payload shape and aggregate math stay identical to the normal
    pending_classifications path -- duplicating that logic here would be a
    correctness risk for no benefit.

    Also reconciles pending_classifications: any row whose qdrant_point_id
    matches an applied point (regardless of its current status) is flipped
    to 'applied', so a stale/orphaned row from a reprocessed document stops
    showing up in `count`'s pending total and can't be redundantly
    resubmitted by the daily submit_pending_batch_classification cron.
    """
    from sqlalchemy import update

    from app.models.pending_classification import PendingClassification
    from app.services.heatmap_ingest.batch_classifier import BatchClassifier
    from app.services.heatmap_ingest.taxonomy import ChunkClassification
    from app.services.vector_store.factory import VectorStoreFactory, VectorStoreType

    manifest: dict[str, dict[str, Any]] = {}
    with open(args.manifest, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            manifest[row["custom_id"]] = row

    classifier = BatchClassifier()
    vector_store = VectorStoreFactory.create(VectorStoreType(settings.VECTOR_STORE_TYPE))

    stats = {"applied": 0, "failed": 0, "skipped_no_manifest": 0}
    per_doc_classifications: dict[int, list[tuple[Any, ChunkClassification]]] = {}
    reconciled_point_ids: list[str] = []

    with open(args.output, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                result = json.loads(line)
            except json.JSONDecodeError as exc:
                logger.warning("Skipping unparseable output line: %s", exc)
                stats["failed"] += 1
                continue

            custom_id = result.get("custom_id")
            manifest_row = manifest.get(custom_id)
            if manifest_row is None:
                logger.warning(
                    "custom_id %s not found in manifest %s -- skipping",
                    custom_id,
                    args.manifest,
                )
                stats["skipped_no_manifest"] += 1
                continue

            point_id = manifest_row["point_id"]
            document_id = manifest_row.get("document_id")

            error = result.get("error")
            if error:
                logger.warning("point %s: batch error %s", point_id, error)
                stats["failed"] += 1
                continue

            try:
                body = result["response"]["body"]
                content = body["choices"][0]["message"]["content"]
                payload = json.loads(content)
                classification = ChunkClassification.model_validate(payload)
            except Exception as exc:  # noqa: BLE001
                logger.warning("point %s: parse error %s", point_id, exc)
                stats["failed"] += 1
                continue

            metadata = classifier._build_payload_metadata(
                classification, row_entity_type=manifest_row.get("entity_type")
            )
            try:
                await classifier._update_metadata_with_retry(
                    vector_store,
                    chunk_ids=[point_id],
                    metadata=metadata,
                    tenant_id=args.tenant_id,
                )
            except Exception as exc:  # noqa: BLE001
                logger.error("point %s: qdrant set_payload failed: %s", point_id, exc)
                stats["failed"] += 1
                continue

            stats["applied"] += 1
            reconciled_point_ids.append(point_id)

            if document_id is not None:
                meeting_date = None
                meeting_date_str = manifest_row.get("meeting_date")
                if meeting_date_str:
                    try:
                        meeting_date = date.fromisoformat(meeting_date_str[:10])
                    except ValueError:
                        meeting_date = None
                fake_pending = SimpleNamespace(
                    document_id=document_id, meeting_date=meeting_date
                )
                per_doc_classifications.setdefault(document_id, []).append(
                    (fake_pending, classification)
                )

    logger.info(
        "Applied %d point(s) to Qdrant; %d failed; %d skipped (no manifest match)",
        stats["applied"],
        stats["failed"],
        stats["skipped_no_manifest"],
    )

    async with AsyncSessionLocal() as db:
        try:
            await classifier._upsert_heatmap_aggregate(db, per_doc_classifications)
            await db.commit()
            logger.info(
                "Upserted heatmap_aggregate for %d document(s)",
                len(per_doc_classifications),
            )
        except Exception:  # noqa: BLE001
            await db.rollback()
            logger.error(
                "heatmap_aggregate upsert failed -- Qdrant payloads above are "
                "still applied and safe; nightly reconcile_heatmap_aggregate "
                "will backfill the aggregate.",
                exc_info=True,
            )

        # Reconcile pending_classifications so stale/orphaned rows (see
        # `count`'s gap warning) stop being re-submitted by the daily cron.
        if reconciled_point_ids and not args.no_reconcile_pending:
            result = await db.execute(
                update(PendingClassification)
                .where(
                    PendingClassification.qdrant_point_id.in_(reconciled_point_ids),
                    PendingClassification.status != "applied",
                )
                .values(status="applied", error_message=None)
            )
            await db.commit()
            logger.info(
                "Reconciled %d pending_classifications row(s) to status='applied'",
                result.rowcount,
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

    c = sub.add_parser(
        "count",
        help="count classified vs. unclassified chunks for a tenant (read-only)",
    )
    c.add_argument("--tenant-id", type=int, required=True)
    c.set_defaults(func=cmd_count)

    e = sub.add_parser(
        "export-qdrant",
        help="Scroll a tenant's Qdrant chunks and build a batch input JSONL",
    )
    e.add_argument("--tenant-id", type=int, required=True)
    e.add_argument("--limit", type=int, default=200, help="max chunks to export (ignored with --shard)")
    e.add_argument("--out", default=None, help="output .jsonl path (single-file mode, no --shard)")
    e.add_argument(
        "--shard",
        action="store_true",
        help="export the ENTIRE matching set, rotating to a new numbered file "
        "under --out-dir every --shard-max-bytes (for full-backlog exports)",
    )
    e.add_argument(
        "--out-dir",
        default=None,
        help="directory for shard files (required with --shard)",
    )
    e.add_argument(
        "--prefix",
        default="shard",
        help="filename prefix for shard files, e.g. tenant4 -> tenant4_0001.jsonl",
    )
    e.add_argument(
        "--shard-max-bytes",
        type=int,
        default=DEFAULT_MAX_BYTES,
        help=f"bytes per shard before rotating (default {DEFAULT_MAX_BYTES}, "
        f"hard-capped at {HARD_MAX_FILE_BYTES} — the universal 200 MB Batch file limit)",
    )
    e.add_argument(
        "--shard-max-tokens",
        type=int,
        default=DEFAULT_SHARD_MAX_TOKENS,
        help=f"estimated input tokens per shard before rotating (default "
        f"{DEFAULT_SHARD_MAX_TOKENS} = 80% of the Tier-3 gpt-4o-mini batch queue "
        f"limit of {TIER3_GPT4O_MINI_BATCH_QUEUE_LIMIT_TOKENS}; lower this if on "
        "a lower tier, e.g. Tier 1 -> 1_600_000, Tier 2 -> 16_000_000)",
    )
    e.add_argument(
        "--shard-max-requests",
        type=int,
        default=HARD_MAX_REQUESTS_PER_BATCH,
        help=f"requests per shard before rotating (hard-capped at "
        f"{HARD_MAX_REQUESTS_PER_BATCH} — the universal Batch per-file request limit)",
    )
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
    s.add_argument(
        "--force",
        action="store_true",
        help="submit even if the estimated input tokens exceed the Tier-3 "
        "gpt-4o-mini batch queue limit (40,000,000)",
    )
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

    aq = sub.add_parser(
        "apply-qdrant",
        help="apply a completed batch's output.jsonl straight to Qdrant by point id "
        "(counterpart to export-qdrant; no pending_classifications row required)",
    )
    aq.add_argument("--output", required=True, help="output.jsonl from `wait`")
    aq.add_argument(
        "--manifest",
        required=True,
        help="matching <shard>.manifest.jsonl from export-qdrant --manifest",
    )
    aq.add_argument("--tenant-id", type=int, required=True)
    aq.add_argument(
        "--no-reconcile-pending",
        action="store_true",
        help="skip flipping matching pending_classifications rows to 'applied'",
    )
    aq.set_defaults(func=cmd_apply_qdrant)

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
