"""One-at-a-time OpenAI Batch API submit for the heatmap chunk classifier.

Submits ~2,000 chunks per batch (HEATMAP_INGEST_BATCH_MAX_BYTES=70MB to stay
under OpenAI's 20M enqueued-token limit for gpt-4o-mini at Tier 2), waits for
the batch to reach `completed` (only then is the token budget freed -- per
OpenAI's docs, `in_progress` batches still count against the queue limit),
then submits the next. On batch failure, calls poll_batch to immediately
reset stranded `submitted` rows back to `pending` so the next iteration
picks them up.

This serializes on completion, so total runtime is roughly:
    N_batches * per_batch_completion_time
With ~56 batches and 10-30 min/batch, expect ~10-28 hours.

Run detached (the run spans hours):

    docker exec -d just-edtech-api python \\
        scripts/heatmap_ingest/submit_pending_one_at_a_time.py \\
        --initial-batch-id batch_6a8ca5b552c08190bbd18ef57df926dc \\
        > /tmp/heatmap_submit.log 2>&1

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


async def submit_loop(classifier: BatchClassifier) -> None:
    """Submit batches one at a time until the pending pool is drained."""
    batch_num = 0
    while True:
        try:
            async with AsyncSessionLocal() as db:
                job = await classifier.submit_pending_batch(db)
                if job is None:
                    logger.info("No more pending; done.")
                    return
                batch_num += 1
                logger.info(
                    "[%s] Submitted %s (%s chunks)",
                    batch_num,
                    job.batch_id,
                    job.chunk_count,
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
    args = parser.parse_args()

    if uses_openrouter():
        logger.error(
            "LLM_API_PROVIDER=openrouter — OpenRouter has no Batch API. "
            "Set LLM_API_PROVIDER=openai and OPENAI_API_KEY."
        )
        return 1

    # Keep batches small enough to stay under OpenAI's 20M enqueued-token
    # limit for gpt-4o-mini (~2,000 chunks per ~70MB batch).
    settings.HEATMAP_INGEST_BATCH_MAX_BYTES = 70 * 1024 * 1024
    logger.info(
        "HEATMAP_INGEST_BATCH_MAX_BYTES overridden to %s bytes",
        settings.HEATMAP_INGEST_BATCH_MAX_BYTES,
    )

    client = OpenAI()

    if args.initial_batch_id:
        await wait_for_initial_batch(client, args.initial_batch_id)

    classifier = BatchClassifier()
    await submit_loop(classifier)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
