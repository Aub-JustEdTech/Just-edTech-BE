"""One-off diagnostic: isolate whether OpenAI Batch API "Cannot find file"
failures correlate with file size, or are a universal account-wide issue.

Submits two batches back-to-back with the exact production request format
(same model, same system prompt via build_batch_request_line):
  1. A tiny 3-line file (~few KB).
  2. A large synthetic file padded to ~150MB (matching HEATMAP_INGEST_BATCH_MAX_BYTES).

Both batches are submitted, then polled to a terminal state. If the tiny
batch succeeds and the large one fails, size is implicated. If both fail
identically, this is very likely the org-wide outage. Neither batch touches
pending_classifications or Qdrant -- this uses throwaway custom_ids and a
harmless real chunk of text, so a "succeeded" tiny batch would actually cost
a few cents but not corrupt any state.

Run:
    docker exec just-edtech-api python scripts/heatmap_ingest/diag_batch_file_probe.py
"""

from __future__ import annotations

import asyncio
import io
import logging
import sys
import time

from openai import AsyncOpenAI

from app.services.heatmap_ingest.prompt import build_batch_request_line, serialize_batch_line
from app.services.llm.client import get_async_openai_client

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", stream=sys.stdout)
logger = logging.getLogger("diag_batch_file_probe")

MODEL = "gpt-4o-mini"
TERMINAL = ("completed", "failed", "expired", "cancelled")


def _line(custom_id: str, text: str) -> str:
    req = build_batch_request_line(
        custom_id=custom_id,
        chunk_text=text,
        entity_type="agenda_item",
        meeting_date=None,
        state=None,
        model=MODEL,
    )
    return serialize_batch_line(req)


async def _submit_and_track(client: AsyncOpenAI, label: str, jsonl_bytes: bytes) -> dict:
    t0 = time.monotonic()
    file_obj = await client.files.create(
        file=(f"{label}.jsonl", io.BytesIO(jsonl_bytes), "application/jsonl"),
        purpose="batch",
    )
    t_upload = time.monotonic() - t0
    logger.info("[%s] uploaded file_id=%s (%s bytes) in %.1fs", label, file_obj.id, len(jsonl_bytes), t_upload)

    f = await client.files.retrieve(file_obj.id)
    logger.info("[%s] file status right after upload: %s", label, f.status)

    t1 = time.monotonic()
    batch = await client.batches.create(
        input_file_id=file_obj.id,
        endpoint="/v1/chat/completions",
        completion_window="24h",
        metadata={"purpose": "diag_probe", "label": label},
    )
    logger.info("[%s] batch %s created (status=%s) in %.1fs", label, batch.id, batch.status, time.monotonic() - t1)
    return {"label": label, "file_id": file_obj.id, "batch_id": batch.id, "bytes": len(jsonl_bytes)}


async def _poll(client: AsyncOpenAI, job: dict) -> None:
    while True:
        b = await client.batches.retrieve(job["batch_id"])
        logger.info("[%s] status=%s", job["label"], b.status)
        if b.status in TERMINAL:
            if b.status == "failed" and b.errors:
                logger.error("[%s] FAILED: %s", job["label"], b.errors)
            else:
                logger.info("[%s] terminal status: %s", job["label"], b.status)
            return
        await asyncio.sleep(15)


async def main() -> int:
    client = get_async_openai_client()

    tiny_lines = [_line(f"diag-tiny-{i}", f"Test chunk {i} for batch file probe diagnostic.") for i in range(3)]
    tiny_bytes = ("\n".join(tiny_lines) + "\n").encode("utf-8")

    # Pad a real request's text field to build a large file without needing
    # real chunk data -- same request shape/model, just many more lines.
    base_text = "Test chunk for batch file probe diagnostic. " * 50
    large_lines = []
    running = 0
    target = 150 * 1024 * 1024
    i = 0
    while running < target:
        line = _line(f"diag-large-{i}", base_text)
        large_lines.append(line)
        running += len(line.encode("utf-8")) + 1
        i += 1
    large_bytes = ("\n".join(large_lines) + "\n").encode("utf-8")
    logger.info("Built large synthetic file: %d lines, %d bytes", len(large_lines), len(large_bytes))

    jobs = []
    jobs.append(await _submit_and_track(client, "tiny", tiny_bytes))
    jobs.append(await _submit_and_track(client, "large", large_bytes))

    await asyncio.gather(*(_poll(client, job) for job in jobs))
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
