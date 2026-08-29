#!/usr/bin/env bash
# Sequential Tier-3-safe backfill loop for the heatmap chunk classifier.
#
# Submits each shard produced by `export-qdrant --shard`, blocks until it
# reaches a terminal OpenAI Batch status, writes the results straight to
# Qdrant, then moves to the next shard -- never more than one shard's worth
# of tokens enqueued at a time (see build_classifier_batch_jsonl.py's
# TIER3_GPT4O_MINI_BATCH_QUEUE_LIMIT_TOKENS comment for why Tier 3 can only
# have ~one batch this size in flight).
#
# Usage (run the export first, then this loop; both via `docker exec` on
# the production `just-edtech-api` container so ./scripts stays live via
# the bind mount -- see CLAUDE.md):
#
#   docker exec -it just-edtech-api python -m scripts.heatmap_ingest.build_classifier_batch_jsonl \
#     export-qdrant --tenant-id 4 --skip-classified --shard \
#     --out-dir /app/scripts/heatmap_ingest/runs/tenant4 --prefix tenant4
#
#   docker exec -it just-edtech-api bash scripts/heatmap_ingest/run_tier3_backfill_loop.sh \
#     4 /app/scripts/heatmap_ingest/runs/tenant4 tenant4
#
# Run it under nohup/screen/tmux for an overnight unattended run, e.g.:
#
#   docker exec -d just-edtech-api bash -c \
#     'nohup bash scripts/heatmap_ingest/run_tier3_backfill_loop.sh 4 \
#     /app/scripts/heatmap_ingest/runs/tenant4 tenant4 \
#     > /app/scripts/heatmap_ingest/runs/tenant4/loop.log 2>&1 &'
#
# Safe to re-run: shards whose .output.jsonl already exists are skipped, so
# an interrupted overnight run can just be restarted the next day.

set -euo pipefail

TENANT_ID="${1:?Usage: $0 <tenant_id> <shard_dir> <prefix>}"
SHARD_DIR="${2:?Usage: $0 <tenant_id> <shard_dir> <prefix>}"
PREFIX="${3:?Usage: $0 <tenant_id> <shard_dir> <prefix>}"

shopt -s nullglob
shards=("${SHARD_DIR}/${PREFIX}"_[0-9][0-9][0-9][0-9].jsonl)
if [ ${#shards[@]} -eq 0 ]; then
  echo "No shards matching ${SHARD_DIR}/${PREFIX}_NNNN.jsonl -- run export-qdrant --shard first." >&2
  exit 1
fi

echo "Found ${#shards[@]} shard(s) in ${SHARD_DIR}"

n=0
for shard in "${shards[@]}"; do
  n=$((n + 1))
  base="${shard%.jsonl}"
  manifest="${base}.manifest.jsonl"
  meta="${base}.meta.json"
  out="${base}.output.jsonl"
  err="${base}.error.jsonl"

  if [ -f "$out" ]; then
    echo "[$n/${#shards[@]}] $shard -- output already exists, skipping (already applied? check $out)"
    continue
  fi

  echo "[$n/${#shards[@]}] $(date -u +%FT%TZ) submitting $shard"
  python -m scripts.heatmap_ingest.build_classifier_batch_jsonl submit \
    --input "$shard" --meta "$meta"

  echo "[$n/${#shards[@]}] $(date -u +%FT%TZ) waiting for batch to complete"
  python -m scripts.heatmap_ingest.build_classifier_batch_jsonl wait \
    --meta "$meta" --out "$out" --error-out "$err"

  echo "[$n/${#shards[@]}] $(date -u +%FT%TZ) applying results to Qdrant"
  python -m scripts.heatmap_ingest.build_classifier_batch_jsonl apply-qdrant \
    --output "$out" --manifest "$manifest" --tenant-id "$TENANT_ID"

  echo "[$n/${#shards[@]}] $(date -u +%FT%TZ) done"
done

echo "All ${#shards[@]} shard(s) processed."
