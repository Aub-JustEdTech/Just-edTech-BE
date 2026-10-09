# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Install dependencies
poetry install && poetry shell

# Run API server (local)
poetry run uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

# Run Celery worker
poetry run celery -A app.celery_app worker --loglevel=info --concurrency=2

# Run Celery beat (scheduled tasks: daily token aggregation, monthly billing)
poetry run celery -A app.celery_app beat --loglevel=info

# Docker (primary dev mode)
docker compose up -d --build
docker compose exec api bash
docker compose logs -f celery-scraper
docker compose logs -f celery-ingest

# Migrations
alembic upgrade head
alembic revision --autogenerate -m "description"
alembic downgrade -1
alembic heads          # ALWAYS check before writing a migration — see Alembic gotchas

# Linting / formatting
poetry run ruff check app/
./quality_check.sh                       # ruff + black + isort + mypy + pytest (needs Poetry)

# Tests
poetry run pytest tests/
poetry run pytest tests/ -m "not live"                 # skip tests that hit the real internet
poetry run pytest tests/test_main.py::test_health_check # single test

# Seed roles + default admin (superadmin@justedtech.com / SuperAdmin123!)
python scripts/seed_roles.py --with-defaults
```

### Testing notes

- `pytest.ini` sets `asyncio_mode = auto` — async tests need no `@pytest.mark.asyncio`.
- The **`live` marker** flags tests that make real HTTP requests to school websites. They are slow and fail on network changes: use `-m "not live"` in any tight loop.
- **Four tests fail on a clean checkout** (`test_main.py` ×2, `test_filename_sanitization.py` ×2). They are pre-existing and unrelated to most work — don't chase them.

## Local environment gotchas

These are not obvious from the config and each one costs an hour if unknown.

**PostgreSQL is not a Compose service.** `docker-compose.yml` defines only `redis`, `qdrant`, `api`, `celery-ingest`, `celery-scraper`, `celery-beat`. Postgres is expected to already be running (locally: natively on the host). Containers reach it via `POSTGRES_SERVER=host.docker.internal` (all four app services declare `extra_hosts`), which does **not** resolve from a host shell — so host-side `alembic`/`pytest` need `POSTGRES_SERVER=localhost` overriding it.

**Published host ports are non-standard** — they avoid colliding with other local stacks:

| Service | Host | Container |
|---|---|---|
| FastAPI | **8013** | 8000 (Swagger at `/docs`) |
| Redis | **6386** | 6379 |
| Qdrant | **6343** / 6344 | 6333 / 6334 |
| PostgreSQL | 5432 | *not containerised* |

**Flower is deliberately removed** from Compose (saves ~150–300 MB and matches production). Run it on demand — see the commented recipe in `docker-compose.yml`.

**Three single files are bind-mounted individually**: `pyproject.toml`, `alembic.ini`, `gunicorn.conf.py`. A single-file bind binds to the *inode*, so any tool that rewrites rather than edits in place leaves the container serving a stale, sometimes truncated copy. After editing any of them run `docker compose up -d --force-recreate api`. `./app` and `./alembic` are directory mounts, so Python changes are live immediately.

**The `api` container runs `alembic upgrade head` on startup.** A failing migration therefore presents as a crash-looping container, not as a migration error.

## Architecture

### Request flow

```
HTTP request
  → FastAPI app (app/main.py)
  → api_router (app/api/api.py)  ← all routes prefixed /api/v1
  → endpoint (app/api/endpoints/*.py)
  → service (app/services/**/)   ← business logic
  → crud (app/crud/*.py)         ← DB queries via SQLAlchemy async
  → PostgreSQL / Qdrant / Redis
```

All responses are wrapped via `success_response()` / `error_response()` from `app/utils/response.py`.

### Registered API domains (all under `/api/v1`)

| Prefix | Endpoint file | Notes |
|--------|--------------|-------|
| `/auth` | `auth.py` | Register, login, OTP, password reset |
| `/chat-auth` | `chat_auth.py` | Anonymous chat consumer registration |
| `/invitations` | `invitations.py` | Tenant admin invite flow |
| `/admin` | `admin.py` | User/tenant management (admin only) |
| `/chatbots` | `chatbots.py` | Chatbot config CRUD |
| `/documents` | `documents.py` | Document upload and management |
| `/conversations` | `conversations.py` | Chat conversation sessions |
| `/rag` | `rag.py` | RAG query endpoint |
| `/heatmap/engine` | `heatmap_engine.py` | District topic-tag counts + citations |
| `/analytics` | `analytics.py` | Usage analytics |
| `/token-usage` | `daily_token_usage.py` | Per-tenant daily token stats |
| `/billing` | `monthly_billing.py` | Monthly billing aggregation |
| `/batches` | `upload_batches.py` | Batch document upload tracking |
| `/api-keys` | `api_keys.py` | API key management |
| `/llm-models` | `llm_models.py` | LLM model registry |
| `/pipeline-status` | `pipeline_status.py` | Document processing pipeline status |
| `/school-scraper` | `school_scraper.py` | Discovery + media scraping (see below) |

### Two authentication systems

| Who | How | Dependency |
|-----|-----|------------|
| Tenant admin users | JWT Bearer token | `get_current_user` in `app/utils/dependencies.py` |
| Chat consumers (end-users) | UUID header/query (`X-Chat-Consumer-UUID`) | `get_chat_consumer_from_uuid` in `app/utils/dependencies.py` |

API keys are issued per chatbot and scoped to a tenant. Chat consumers are anonymous sessions registered via `/api/v1/chat-auth/register`.

### RAG pipeline

Document upload → S3 (stored) + Celery task queued → worker runs `DocumentProcessingService` (chunking via `app/services/document_processing/chunker.py`) → embeddings via `OpenAIEmbeddingService` → vectors stored in Qdrant collection `{QDRANT_COLLECTION_PREFIX}_{tenant_id}`.

Query path: `conversations` endpoint → `ChatService` → `AgenticRAGService` (LangGraph agent with `AsyncPostgresSaver` checkpointing per `conversation_id` as `thread_id`) → vector similarity search → LLM response + citations.

### Document pipeline conventions (`app/tasks/document_pipeline.py`)

The pipeline is a 6-stage Celery chain. Three conventions are load-bearing and easy to break:

**`document_type` must include the leading dot.** Stage 1 builds `temp_file_path = f"{uuid}{document_type}"` and `ProcessorFactory` keys on `Path(...).suffix`. Passing `"txt"` yields `"abc123txt"`, whose suffix is `""`, and the factory raises — so any producer must pass `".txt"`, `".pdf"`, `".transcript"`.

**`doc_metadata` keys prefixed with `_` are internal** and stripped before the payload is spread onto every Qdrant chunk. Use the prefix for anything large or per-chunk (`_pdf_pages_text`, `_xlsx_pre_chunks`, `_transcript_pre_chunks`); omitting it copies the value onto every vector.

**Pre-chunking is the extension point for per-chunk metadata.** A processor that needs chunk boundaries the generic `Chunker` cannot recover writes `_<kind>_pre_chunks` + `_<kind>_chunk_meta` in stage 2; stage 3 consumes them in a single `if/elif` chain (transcript → xlsx → per-page PDF → generic). The per-chunk dict is merged into the Qdrant payload, which is how `page_number`, `sheet_name` and `start_ms` reach a citation.

### School scraper — two distinct layers

Reading one file makes this look like a single flow; it is two, with a human step between them.

**Layer 1 — which pages?** (built, run once per district) Each school has one
district homepage (`schools.website`) and zero or more confirmed scrapable
archive URLs (`school_scrape_urls`). Discovery starts from `website`; humans
confirm one or more candidates into `school_scrape_urls`.
```
POST /school-scraper/discover   (input: school website URL)
  → /wp-sitemap.xml → /sitemap.xml → robots.txt → homepage nav crawl
  → keyword filter, then ranking per SCHOOL_SCRAPER_RANKING_MODE (keyword | llm | both)
  → candidates JSON (SCHOOL_URL_CANDIDATES_JSON_PATH)
  → human verification → school_scrape_urls (may be multiple per district)
```
`llm`/`both` modes route through `schema_driven_crawler.py` + `page_classifier.py`; `keyword` is the zero-cost default and switching back is a config-only rollback.

**Layer 2 — what's on it?** Media on each confirmed scrapable URL, re-crawled as
new meetings post. Periodic fetches **only** walk active `school_scrape_urls`
rows — they do not re-run Layer 1 discovery.
```
POST /school-scraper/scrape-media   (persist=false → preview only; the default)
  → scraped_media rows (persist=true, needs school_id)
  → ingest_scraped_media → transcript/text → Document → document pipeline
```
`sweep_school_media` runs Layer 2 across every active scrape URL for every
district. It is intentionally **not** in `beat_schedule` — run it manually and
check the created/skipped counts first.

Idempotency is the cost control: `create_scraped_media` returns `(row, created)` and **only rows with `created=True` may be enqueued**, or every re-crawl re-pays for the whole corpus. Dedup is `(school_id, url_hash)` and `(school_id, content_hash)` — **per school**, so two districts sharing a URL each pay separately.

Helper scripts live in `scripts/school_data/` (`seed_schools.py`, `discover_school_candidates.py`, `confirm_scrape_urls.py`, `feed_finalised_scrape_urls.py`).

### Transcription (`app/services/transcription/`)

Audio/video → timestamped, speaker-labelled transcript. Cost gates run cheapest-first; only an item failing all of them costs money (~$0.23/audio-hour):

1. already in `scraped_media` → skip
2. YouTube → `youtube-transcript-api`; **any** captions, manual or auto, are free
3. duration cap via a **remote** `ffprobe` header read (~1.5 s, no download) → over cap, no spend
4. AssemblyAI `universal-3-5-pro` → `universal-2` availability fallback

Non-obvious constraints, each with a test guarding it:

- **The transcript is stored as a JSON envelope, never flat text.** Flattening destroys timestamps and speaker labels irreversibly — recovering them means re-transcribing and paying again. `TranscriptResult.to_envelope()` / `from_envelope()` in `schemas.py` are the only serialisation path.
- **No custom vocabulary is ever sent** — no `keyterms_prompt`, `word_boost` or `custom_spelling`. `speaker_labels=True` is required.
- **`FORBIDDEN_FILTERS` must stay out of the ffmpeg chain.** `silenceremove`/`atrim` shift the timeline and silently break every later timestamp; `loudnorm`/`dynaudnorm` measurably *lower* SNR. Gain is linear only.
- **`TRANSCRIPTION_AUDIO_MODE=url_direct` (default) never downloads media** — AssemblyAI fetches the URL itself, so the worker uses ~0 CPU and no temp disk. `preprocess` is the download+denoise fallback.
- **A caption fetch that fails must fail loudly.** "No captions" and "captions unreachable" are indistinguishable from the outside; conflating them routes the whole corpus to the paid path with normal-looking logs. Rate-limit errors raise (Celery retries); only genuine absence returns `None`.

Terminal vs transient errors drive retry behaviour: `TerminalTranscriptionError` subclasses carry a `.status` written to `ScrapedMedia.status` and the task returns **without** raising; `TranscriptionProviderError` propagates so Celery retries with backoff.

### Services layer structure

- `app/services/agentic_rag/` — LangGraph agent (graph, nodes, tools, prompts, state)
- `app/services/document_processing/` — chunking, PDF/DOCX/XLSX/PPTX/transcript processors, factory
- `app/services/transcription/` — YouTube captions + AssemblyAI (see above)
- `app/services/vector_store/` — abstraction over Qdrant/Chroma (factory pattern, `VECTOR_STORE_TYPE` selects)
- `app/services/llm/` — LLM provider abstraction (factory pattern, OpenAI provider)
- `app/services/embeddings/` — embedding service
- `app/services/web_scraper/` — school scraper, schema-driven crawler, page classifier, year inference
- `app/services/observability/` — LangSmith tracing initialization
- `app/services/chatbot_config_service.py` — chatbot configuration management
- `app/services/token_tracking_service.py` — per-message token counting

### Multi-tenancy

Every chatbot, document, conversation, and vector collection is scoped to a `tenant_id`. The Qdrant collection name is `{QDRANT_COLLECTION_PREFIX}_{tenant_id}` (default prefix: `justedtech`). `DEFAULT_TENANT_ID=1` is the fallback tenant for seeded data.

Any endpoint loading a record by id must scope the query by `tenant_id` — without it one tenant can read or mutate another's rows by guessing an id.

## Celery

### Three workers, three queues

Routing lives in `celery_app.conf.task_routes`; a task with no route lands on `celery`.

| Service | Queues | Concurrency | Soft limit | Carries |
|---|---|---|---|---|
| `celery-ingest` | `celery`, `documents` | 1 | 3000 s | Document pipeline, billing aggregation |
| `celery-scraper` | `scraping` | **1 per replica** | 6000 s | School scraping + transcription |
| `celery-beat` | — | — | — | Scheduler only |

**Bulk ingest:** scale worker *replicas*, not `--concurrency`. On **t4g.2xlarge (8 vCPU, 32 GiB)**:

```bash
# Batch ingest (3 doc pipelines + 2 scrape/transcribe lanes in parallel)
docker compose -f docker-compose.prod.yml up -d \
  --scale celery-ingest=3 --scale celery-scraper=2

# Steady state after batch
docker compose -f docker-compose.prod.yml up -d \
  --scale celery-ingest=1 --scale celery-scraper=1
```

| t4g.2xlarge layout | Replicas | Concurrency | Parallel lanes |
|---|---|---|---|
| `celery-ingest` | 3 | 1 | 3 document pipelines |
| `celery-scraper` | 2 | 1 | 2 download/transcribe jobs |

**`celery-scraper` at concurrency 1 per replica** is intentional — Playwright/transcription is memory-heavy. A 3-hour video still blocks one replica, not the whole fleet. Both worker services share the `temp_uploads` volume, so a leaked multi-GB temp file takes down both — always clean up in a `finally`.

### Registering a task

`celery_app.autodiscover_tasks(["app.tasks"])` looks only for a submodule literally named `tasks` (i.e. `app.tasks.tasks`), which does not exist. **Every task module must be imported in `app/tasks/__init__.py`** or its tasks are never registered and beat-sent messages are discarded with `KeyError`.

Per `.claude/skills/add-celery-task`: `bind=True`, `max_retries=3`, backoff `countdown=60 * (2 ** self.request.retries)`, bridge async via `app/tasks/loop_utils.py:get_event_loop()`, and open a fresh `AsyncSessionLocal()` *inside* the async impl — never pass a session in.

### Scheduled tasks

- **Daily 2:00 AM UTC** — `aggregate_daily_token_usage`
- **1st of month 3:00 AM UTC** — `aggregate_monthly_billing`
- **Daily 4:00 AM UTC** — `submit_pending_batch_classification`; polled every 15 min
- **Daily 3:30 AM UTC** — `reconcile_heatmap_aggregate` (recomputes from Qdrant to catch drift)

Celery broker and backend both use Redis DB `/2` (separate from app Redis on DB `3`).

## Alembic gotchas

**Parts of this database were created by `Base.metadata.create_all()`**, which does not write to `alembic_version`. Consequences:

- `alembic upgrade head` can fail with `DuplicateTable` on a revision whose tables already exist. The fix is `alembic stamp <that revision>` — but only after confirming every table it creates really exists.
- **`--autogenerate` produces dangerous false positives.** It will propose dropping the LangGraph `checkpoint`, `checkpoint_blobs`, `checkpoint_writes` and `checkpoint_migrations` tables — they are created at runtime by `langgraph-checkpoint-postgres`, are not in `Base.metadata`, and dropping them destroys conversation state. It also churns unrelated indexes. **Always read the generated file and delete everything outside the intended change.**

Run `alembic heads` before writing a migration: `down_revision` must be the current head, not the newest-looking filename. Filenames mix date-prefixed (`20260727_000001_*`) and hash (`9d23df92a0ce_*`) styles, so the chain is not alphabetical. Two revisions claiming the same parent creates two heads and breaks `upgrade head`.

Never edit an applied migration — `alembic_version` then disagrees with the file chain.

## Other traps

- **`crud/schools.py:update_scraped_media` skips `None` values.** To clear a field (e.g. `error_message` on a successful retry) pass `""`, not `None`.
- Import task modules **inside** endpoint function bodies (as `backfill_years` and `scrape_media` do) to avoid the tasks → models import cycle.
- Enqueue a Celery task only **after** the DB commit, or the worker's `db.get(...)` returns `None` and silently drops the item.

## Claude Code customisations

Rules and skills live in `.claude/` and are loaded automatically. `.cursor/rules/*.mdc` mirror the same content for Cursor.

### Rules (always-on)

Architecture and layout: `backend-core-rules`, `fastapi-architecture`, `service-layer`, `repository-pattern`, `dependency-injection`
API and schemas: `api-rules`, `api-endpoints`, `pydantic-schemas`, `type-safety`
Data: `database-rules`, `database-patterns`
Patterns: `factory-pattern`, `strategy-pattern`
Process: `code-quality-rules`, `error-handling`, `gitops-rules`

Two constraints from `gitops-rules` that are easy to violate accidentally: **never commit to `main`/`master`/`develop`** (branches are `{type}_{kebab-description}`, commits are Conventional Commits), and **do not reformat, delete or rename files unless explicitly instructed** — which rules out running `ruff format` across a directory to tidy a diff.

### Skills (on-demand)

| Skill | Activate by asking to… |
|---|---|
| `add-api-endpoint` | Add a route / build a new domain end-to-end |
| `add-celery-task` | Run something in the background / add a scheduled job |
| `create-migration` | Add a model / change a column / create or apply a migration *(Bash+Read only)* |
| `add-error-handling` | Add exception handling / custom exception types |
| `implement-factory-pattern` | Add a factory / pluggable provider selection |
| `implement-strategy-pattern` | Add interchangeable strategies behind one interface |
| `code-review` | Review backend code / self-review before merging *(read-only)* |
| `github-pr-create` | Open a PR |
| `github-pr-review` | Review a PR / check what a branch changes |

## Key env vars

Required (no usable default in `.env.example`):
- `SECRET_KEY` — JWT signing key
- `OPENAI_API_KEY`
- `S3_BUCKET_NAME`, `S3_REGION`, `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`
- `BACKEND_CORS_ORIGINS` — JSON array of allowed origins
- `ASSEMBLYAI_API_KEY` — required only for audio/video transcription

Notable switches:
- `VECTOR_STORE_TYPE=qdrant` is the production default; `chroma` is local-only
- `SCHOOL_SCRAPER_RANKING_MODE` — `keyword` (default) | `llm` | `both`
- `TRANSCRIPTION_AUDIO_MODE` — `url_direct` (default) | `preprocess`
- `SCHOOL_SCRAPER_ALLOWED_YEARS` — download-time year filter; out-of-range docs get `status="skipped_year"` and are never ingested
- `SCHOOL_SCRAPER_MEDIA_MAX_DURATION_MINUTES` — the pre-spend transcription cost cap

Further docs in `docs/`: `BACKGROUND_WORKERS_SETUP.md`, `DOCUMENT_INGESTION_ARCHITECTURE.md`, `DOCUMENT_INGESTION_API.md`, `QDRANT_SETUP.md`, `TOKEN_BILLING_SYSTEM.md`.
