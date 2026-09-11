"""One-at-a-time OpenAI Batch API submit for the heatmap chunk classifier.

Submits ~4,300 chunks per batch (HEATMAP_INGEST_BATCH_MAX_BYTES=150MB to stay
under OpenAI's 40M enqueued-token limit for gpt-4o-mini at Tier 3 -- 75%
utilization leaves headroom for chunk-size variance), waits for the batch to
reach `completed` (only then is the token budget freed -- per OpenAI's docs,
`in_progress` batches still count against the queue limit), then submits the
next. On batch failure, calls poll_batch to immediately reset stranded
`submitted` rows back to `pending` so the next iteration picks them up.

This serializes on completion, so total runtime is roughly:
    N_batches * per_batch_completion_time
With ~21 batches and 10-30 min/batch, expect ~3.5-10 hours.

Run detached (the run spans hours):

    docker exec -d just-edtech-api sh -c 'python \\
        scripts/heatmap_ingest/submit_pending_one_at_a_time.py \\
        > /tmp/heatmap_submit.log 2>&1'

For Tier 2 (20M limit), pass --batch-max-mb 70 to use ~2,000-chunk batches.

Pass --max-chunks N to stop after ~N chunks have been submitted this run
instead of draining the entire pending pool (e.g. --max-chunks 111000 for a
first, smaller pass). The cap is checked before each batch, so the final
batch can slightly overshoot N. Remaining pending rows are left untouched --
re-run later (with a higher/no cap) to continue.

Tail progress:

    docker exec just-edtech-api cat /tmp/heatmap_submit.log

Requires LLM_API_PROVIDER=openai (OpenRouter has no Batch API).
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys

from openai import OpenAI

from app.core.config import settings
from app.db.connector import AsyncSessionLocal
from app.services.heatmap_ingest.batch_classifier import BatchClassifier
from app.services.llm.client import uses_openrouter

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger("submit_pending_one_at_a_time")

# Max bytes for one OpenAI Batch input JSONL. Sized for the active OpenAI
# tier's enqueued-token queue limit for gpt-4o-mini. With a ~24 KB system
# prompt, each line is ~35 KB:
#   - Tier 2 (20M tokens): 70 MB -> ~2,000 chunks/batch (~14M tokens, 70%)
#   - Tier 3 (40M tokens): 150 MB -> ~4,300 chunks/batch (~30M tokens, 75%)
# 75% utilization leaves headroom for chunk-size variance. Two batches
# in flight would exceed the limit (60M > 40M), so keep one-at-a-time.
#
# Override via CLI for one-off runs without editing this default.
DEFAULT_BATCH_MAX_BYTES = 150 * 1024 * 1024

# Terminal OpenAI Batch API statuses.
TERMINAL = ("completed", "failed", "expired", "cancelled")
# When the inner poll loop can break and submit the next batch.
#
# IMPORTANT: only `completed` (and failure states) free the enqueued-token
# budget. OpenAI's docs: "Tokens from pending batch jobs are counted against
# your queue limit. Once a batch job is completed, its tokens are no longer
# counted against that model's limit." A batch in `in_progress` STILL holds
# its tokens against the 20M Tier-2 limit. Submitting the next batch while
# the previous one is `in_progress` (rather than `completed`) causes the
# next batch to fail validation with `Enqueued token limit reached`. With
# ~14M tokens per 2,000-chunk batch, two in-flight = 28M > 20M limit.
SUBMIT_READY = TERMINAL

INITIAL_POLL_INTERVAL_S = 30
# Inner poll interval for batches we've submitted. Longer than the initial
# poll because batch completion takes 10-30 min; polling every 15s just
# adds noise to the log.
SUBMIT_READY_POLL_INTERVAL_S = 30
FAILURE_BACKOFF_S = 30
ITERATION_ERROR_BACKOFF_S = 60


async def wait_for_initial_batch(client: OpenAI, batch_id: str) -> str:
    """Block until the seeded initial batch reaches a terminal status."""
    logger.info("Waiting for initial batch %s to complete...", batch_id)
    while True:
        b = client.batches.retrieve(batch_id)
        counts = b.request_counts
        if counts:
            logger.info(
                "  status=%s completed=%s/%s",
                b.status,
                counts.completed,
                counts.total,
            )
        else:
            logger.info("  status=%s (no counts yet)", b.status)
        if b.status in TERMINAL:
            break
        await asyncio.sleep(INITIAL_POLL_INTERVAL_S)
    logger.info("Initial batch finished: %s", b.status)
    return b.status


async def submit_loop(
    classifier: BatchClassifier, max_chunks: int | None = None
) -> None:
    """Submit batches one at a time until the pending pool is drained.

    max_chunks: stop once this many chunks have been *submitted* (checked
    before each new submit, not mid-batch -- the last batch can slightly
    overshoot since submit_pending_batch pulls a fixed-size slice). Pass
    None (default) to drain the entire pending pool, matching the original
    batch-1 behavior.
    """
    batch_num = 0
    total_chunks_submitted = 0
    while True:
        if max_chunks is not None and total_chunks_submitted >= max_chunks:
            logger.info(
                "Reached --max-chunks %s (%s submitted); stopping before the "
                "next batch. Remaining pending rows are untouched -- re-run "
                "without --max-chunks (or with a higher value) to continue.",
                max_chunks,
                total_chunks_submitted,
            )
            return
        try:
            async with AsyncSessionLocal() as db:
                job = await classifier.submit_pending_batch(db)
                if job is None:
                    logger.info("No more pending; done.")
                    return
                batch_num += 1
                total_chunks_submitted += job.chunk_count
                logger.info(
                    "[%s] Submitted %s (%s chunks, %s total this run)",
                    batch_num,
                    job.batch_id,
                    job.chunk_count,
                    total_chunks_submitted,
                )

                while True:
                    batch = await classifier._client.batches.retrieve(job.batch_id)
                    logger.info("  status=%s", batch.status)
                    if batch.status in SUBMIT_READY:
                        break
                    await asyncio.sleep(SUBMIT_READY_POLL_INTERVAL_S)

                if batch.status == "completed":
                    # Results are applied by the 15-min Celery poller
                    # (poll_batch_classification). No action needed here.
                    pass
                elif batch.status in ("failed", "expired", "cancelled"):
                    logger.warning(
                        "  Failed: %s; resetting rows via poll_batch",
                        batch.status,
                    )
                    # submit_pending_batch already flipped the rows to
                    # 'submitted' and committed. poll_batch is the only path
                    # that resets stranded 'submitted' rows back to 'pending'
                    # (with retry_count bump + dead_letter guard). Without
                    # this call those rows stay 'submitted' until the 15-min
                    # Celery poller rescues them -- which strands them at the
                    # tail if the pending pool drains first.
                    await classifier.poll_batch(db, job.batch_id)
                    await asyncio.sleep(FAILURE_BACKOFF_S)
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "Iteration error: %s; retrying in %ss",
                exc,
                ITERATION_ERROR_BACKOFF_S,
                exc_info=True,
            )
            await asyncio.sleep(ITERATION_ERROR_BACKOFF_S)


async def main() -> int:
    parser = argparse.ArgumentParser(
        description="Submit heatmap classifier batches one at a time.",
    )
    parser.add_argument(
        "--initial-batch-id",
        type=str,
        default=None,
        help="A batch already in flight to wait for before starting the submit loop.",
    )
    parser.add_argument(
        "--batch-max-mb",
        type=int,
        default=DEFAULT_BATCH_MAX_BYTES // (1024 * 1024),
        help=(
            "Max JSONL size per batch in MB. Default %(default)sMB (Tier 3, "
            "~4,300 chunks/batch at ~30M tokens, 75%% of the 40M limit). "
            "Use 70 for Tier 2 (20M limit, ~2,000 chunks/batch)."
        ),
    )
    parser.add_argument(
        "--max-chunks",
        type=int,
        default=None,
        help=(
            "Stop submitting once this many chunks have been submitted this "
            "run (checked before each batch, so the last batch can slightly "
            "overshoot). Default: drain the entire pending pool (original "
            "batch-1 behavior). Remaining pending rows are left untouched -- "
            "re-run later (with a higher value or no cap) to continue."
        ),
    )
    args = parser.parse_args()

    if uses_openrouter():
        logger.error(
            "LLM_API_PROVIDER=openrouter — OpenRouter has no Batch API. "
            "Set LLM_API_PROVIDER=openai and OPENAI_API_KEY."
        )
        return 1

    settings.HEATMAP_INGEST_BATCH_MAX_BYTES = args.batch_max_mb * 1024 * 1024
    logger.info(
        "HEATMAP_INGEST_BATCH_MAX_BYTES overridden to %s MB (%s bytes)",
        args.batch_max_mb,
        settings.HEATMAP_INGEST_BATCH_MAX_BYTES,
    )

    client = OpenAI()

    if args.initial_batch_id:
        await wait_for_initial_batch(client, args.initial_batch_id)

    if args.max_chunks is not None:
        logger.info("Capping this run at %s chunks (--max-chunks)", args.max_chunks)

    classifier = BatchClassifier()
    await submit_loop(classifier, max_chunks=args.max_chunks)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
