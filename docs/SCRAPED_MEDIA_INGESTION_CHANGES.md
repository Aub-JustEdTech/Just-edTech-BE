# Scraped-Media Ingestion Pipeline — Change Log

**Branch:** `feat_bulk-scraped-media-ingestion`
**Owner:** Dhruvi
**Related:** the upstream scraping pipeline writes rows into `scraped_media`; this branch covers everything from "a row exists" through "it's chunked, embedded, tagged, and queryable."

This is a running log — update it as work continues on this branch, don't start a new file.

---

## 1. New: bulk ingestion driver script

**File:** `scripts/school_data/bulk_ingest_scraped_media.py`

Reads `scraped_media` rows for a tenant (default filter: `status='discovered'`), and dispatches the existing `ingest_scraped_media` Celery task for each one — no new ingestion logic, just orchestrates what already existed as a single-item task.

- `--dry-run` — preview what would be dispatched, no writes, no Celery, no LLM calls.
- `--school-id` / `--limit` — scope to a subset (testing, or a single district).
- `--batch-size` / `--pause-seconds` — paginate the DB query and throttle dispatch rate.
- `--reset-stale-minutes` — resets rows stuck in `downloading`/`ingesting` (from a crashed worker) back to `discovered` before dispatching.

Intended use: the one-time week-1 bulk run across all 400 districts once the upstream scrape pass is confirmed done, and later the same core logic can back a scheduled 15-day recheck sweep or a manual-trigger endpoint.

## 2. New: per-district ingestion status

**Files:** `app/crud/schools.py`, `app/api/endpoints/pipeline_status.py`

- `count_scraped_media_by_status(db, tenant_id)` — tenant-wide `{status: count}` rollup.
- `list_stale_in_progress_media(db, tenant_id, older_than_minutes)` — finds rows stuck in a transient status.
- `scraped_media_status_by_school(db, tenant_id)` — per-district rollup (status counts per school), sorted by backlog (`discovered` count) descending.
- `GET /api/v1/pipeline/scraped-media/districts` — exposes the per-district rollup, tenant-scoped, same auth/response pattern as the rest of `pipeline_status.py`.

Fills a real gap: there was no way to see "which of the 400 districts are behind on ingestion" — only per-document/per-batch status existed before.

## 3. New: LangSmith tracing for ingestion-time LLM calls

**Files:** `app/services/heatmap_ingest/doc_classifier.py`, `app/services/document_processing/summarizer.py`, `app/services/heatmap_ingest/contextualizer.py`

Added `@traceable` to the three LLM call sites in the per-document pipeline (`DocClassifier.classify`, `DocumentSummarizer._call_llm`, `Contextualizer._augment_one`). Before this, only the live RAG chat-query path (`llm_service.py`, `agentic_rag/service.py`) was visible in LangSmith — ingestion-time calls (summarize/classify/contextualize) were completely dark.

**Known limitation:** `@traceable` only wraps the Python function boundary — it doesn't capture token/cost, because these call the raw `AsyncOpenAI` client directly rather than through a LangSmith-wrapped client. Getting token/cost visibility would need `langsmith.wrappers.wrap_openai()` applied to the client instances — not done yet.

**Not covered:** the chunk-level taxonomy classifier (`batch_classifier.py`) — it submits to OpenAI's Batch API (async file-in/file-out), which doesn't fit LangSmith's per-call trace model. Batch jobs are visible instead at platform.openai.com/batches.

## 4. Fix: `normalize_model_name()` didn't strip the OpenRouter prefix under direct OpenAI

**File:** `app/services/llm/client.py`

**Bug:** every model-config default in the codebase (`CHATBOT_DEFAULT_CHAT_MODEL`, `HEATMAP_INGEST_DOC_CLASSIFIER_MODEL`, `OPENAI_EMBEDDING_MODEL`, etc.) is written as `"openai/gpt-4o-mini"` — OpenRouter's namespaced format. `normalize_model_name()` only knew how to *add* that prefix for OpenRouter; it never stripped it when `LLM_API_PROVIDER=openai`. After flipping the Batch API provider to `openai` (this session), every direct-OpenAI-client call (chat completions *and* embeddings) started failing with `invalid model ID` — including the live RAG chat path for any chatbot using the default model.

**Fix:** made the function symmetric — strips the prefix when not using OpenRouter, adds it when using OpenRouter and missing.

**Impact:** this was a live-chat-breaking bug, not just an ingestion issue, once the provider was switched.

## 5. Fix: silent data loss on large Qdrant upserts

**Files:** `app/services/vector_store/qdrant_store.py`, `app/core/config.py`

**Bug:** `QdrantStore.add_chunks_returning_ids` sent **all chunks of a document in one upsert call**. For a 342-chunk document, this exceeded the Qdrant client's default write timeout (`httpx.WriteTimeout`). The exception was caught by a blanket `except Exception: return []` and swallowed — so the pipeline logged "Stage 5 completed: Stored N chunks" and marked `Document.processing_status = COMPLETED`, while **zero vectors were actually written**. Confirmed via direct Qdrant query: a `COMPLETED` document had 0 points in its collection.

This is not specific to school-scraper docs — every document (manual uploads too) goes through this same path.

**Fix:**
- Upserts are now batched (`QDRANT_UPSERT_BATCH_SIZE`, default 100 points/call) — new setting in `app/core/config.py`.
- The outer catch-all that swallowed real I/O failures was removed. A failed batch now raises, so Celery's existing retry logic in `step5_store_vectors` (already there, previously never triggered) engages, and `Document.processing_status` correctly ends up `FAILED` instead of a false `COMPLETED`.

**Verified:** re-ran a 342-chunk document after the fix — 342 points confirmed in Qdrant (was 0 before), and 342 rows correctly landed in `pending_classifications`.

## 6. Fix: OpenAI Batch API rejected every request (`"method": "post"`)

**File:** `app/services/heatmap_ingest/prompt.py`

**Bug:** `build_batch_request_line()` set `"method": "post"` (lowercase) in each JSONL line. OpenAI's Batch API requires uppercase `"POST"` — every line in the first real submission failed validation (`Invalid value: 'post'. Supported values are: 'POST'.`), and the batch failed ~60s after being created.

**Fix:** one-line change to `"POST"`.

**Verified:** resubmitted the same 342-chunk batch after the fix — live status confirmed `in_progress` with `342/342` requests accepted, 0 failed.

## 7. Data fix (not code): created a default `ChatbotConfig` for tenant 2

Chunking (`step3_chunk_text`) requires a tenant's default chatbot config to read chunk-size settings from. Tenant 2 had **zero** chatbot configs in this dev DB, so every document (any source) failed at the chunking stage with `Default chatbot config not found for tenant 2`. Created one minimal config (`is_default=True`, default chunking settings) via the existing `chatbot_config.create()` CRUD method — no code change, just missing dev-environment data.

## 8. Config change (not code): `LLM_API_PROVIDER` flipped to `openai`

Done directly in `.env` by Dhruvi (not read/touched by Claude, per instruction). `docker-compose restart <service>` does **not** pick up `.env` changes — only `docker compose up -d <service>` (recreates the container) does. Worth remembering; the CLAUDE.md command reference implies `restart` is sufficient for "config changes," which isn't true for env vars specifically.


## 9. Fix: same silent-swallow pattern, third occurrence — `QdrantStore.update_metadata`

**File:** `app/services/vector_store/qdrant_store.py`

**Bug:** `update_metadata()` (used by `apply_batch_results` to write classification tags onto Qdrant points via `set_payload`) caught any exception internally and returned `False` — but its only two callers (`batch_classifier.py`, both already wrapped in their own `try/except`) never checked that boolean. A failed metadata write would still fall through to `pending.status = "applied"`, same false-success shape as fix #5, just one layer up. Never triggered yet since no batch had completed successfully before this session.

**Fix:** removed the internal swallow — raises now, so the existing caller-side `try/except` correctly marks the row `failed` instead of `applied`.

## 10. Fix: failed/expired/cancelled batches stranded their chunks forever

**File:** `app/services/heatmap_ingest/batch_classifier.py` (`poll_batch`)

**Bug:** when a batch ended in `failed`/`expired`/`cancelled`, its `pending_classifications` rows stayed at `status='submitted'` pointing at a dead `batch_id` — nothing ever reset them to `pending`, so they'd never be retried. Confirmed manually earlier this session (had to hand-reset 342 rows after the `"post"`/`"POST"` bug killed the first real submission).

**Fix:** `poll_batch()` now resets any `submitted` rows tied to a batch that ends `failed`/`expired`/`cancelled` back to `pending` (clearing `batch_id`, recording the reason in `error_message`), so the next submit run automatically picks them back up.

**Verified live**: submitted a 342-chunk batch, manually cancelled it via the OpenAI dashboard, ran `poll_batch` — log confirmed `"Batch ... ended cancelled; reset 337 chunk(s) back to pending for resubmission"` (337, not 342, because 5 had already been pulled out separately for a small-batch test). DB check confirmed: 337 rows back at `pending`, 5 at `submitted` for the small test batch.

## 11. New: retry/dead-letter cap on the `poll_batch` reset logic

**Files:** `app/models/pending_classification.py`, `app/core/config.py`, `app/services/heatmap_ingest/batch_classifier.py`, `alembic/versions/e555c8a175ee_add_retry_count_to_pending_.py`

**Gap (flagged in fix #10):** the reset-to-`pending` logic added for fix #10 had no cap — a batch failing repeatedly for a *content* reason (not a transient bug) would reset the same chunks back to `pending` forever, retrying on every daily submit with no way to stop.

**Fix:** added `PendingClassification.retry_count` (Integer, default 0) and `HEATMAP_INGEST_MAX_BATCH_RETRIES` (default 3). `poll_batch()` now increments `retry_count` on every reset; once it exceeds the cap the row is parked at a new terminal status, `dead_letter` (batch_id cleared, reason recorded in `error_message`), instead of going back to `pending`. `dead_letter` rows are excluded from both submit paths (`submit_pending_batch` / `_submit_pending_via_direct_api`) since those only select `status='pending'`, so they stop being retried automatically without being deleted.

**Verified:** migration applied cleanly (`retry_count` column confirmed via `information_schema.columns`); `poll_batch`'s new branch reviewed by hand (not yet exercised against a real 4th consecutive failure — would need 4 cancelled batches in a row to trigger the `dead_letter` path live).

## 12. New: manual-trigger API endpoint for ingestion

**Files:** `app/schemas/school_scraper.py`, `app/api/endpoints/school_scraper.py`

**Gap (flagged as a "still open" item):** only two triggers existed for ingestion — the CLI script (`scripts/school_data/bulk_ingest_scraped_media.py`) and the Celery-beat 15-day recheck sweep — no way to kick off ingestion for a district (or a small test batch) without shell access to the box running Celery.

**Fix:** `POST /api/v1/school-scraper/ingest` (admin-gated via `get_current_tenant_admin`, mirrors `/backfill-years`). Body: `school_id` (optional), `status` (`"discovered"` or `"failed"` only — `"skipped_year"` rows are deliberately excluded since they need `/backfill-years`'s year re-evaluation, not a raw re-dispatch), `limit` (1–1000, default 200), `reset_stale_minutes` (optional, resets `downloading`/`ingesting` rows stuck past that many minutes back to `discovered` first). Dispatches the same `ingest_scraped_media` Celery task the CLI script and `/backfill-years` already use — no new ingestion logic. Tenant-scoped via `current_user.tenant_id` (never a client-supplied tenant_id), consistent with the rest of `school_scraper.py`.

**Verified live:** called with `school_id=999999` (0 matches) to confirm auth/tenant-scoping/response shape with no side effects; confirmed `status="skipped_year"` and `limit=5000` are both rejected with `422`; then called for real with `limit=1` against tenant 2's 4 waiting `discovered` rows — Celery received and completed the task (`scraped_media_id=7 → document_id=5 → status=completed`) within ~11s, confirmed via both the task's return value and a direct DB check.

---

## In progress

- Watching a small (5-chunk) test batch (`batch_6a7b167809708190b8fbde79ef8f4146`) run end-to-end through `apply_batch_results` for the first time — that handler has never processed a real completed batch before, so this is the last unverified leg of the pipeline. **Still in `in_progress` on OpenAI's side as of this update** (0/5 completed) — not a bug, just not done yet.
- The 337 reset chunks from the cancelled big batch are sitting at `pending`, ready for a full resubmission once the small-batch test confirms `apply_batch_results` is solid.
