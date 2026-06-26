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

# Docker (primary dev mode) — ports: API 8013, PG 5436, Redis 6386, Qdrant 6343
docker-compose up -d --build
docker-compose restart api        # hot-reload if needed after config changes
docker-compose exec api bash

# Migrations
poetry run alembic upgrade head
poetry run alembic revision --autogenerate -m "Description"
poetry run alembic downgrade -1

# Linting / formatting
poetry run ruff check app/
poetry run ruff format app/
poetry run black .

# Tests
poetry run pytest tests/
poetry run pytest tests/test_main.py::test_name  # single test

# Seed roles + default admin (superadmin@justedtech.com / SuperAdmin123!)
python scripts/seed_roles.py --with-defaults
```

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
| `/heatmap` | `heatmap.py` | District/county heatmap + citations |
| `/analytics` | `analytics.py` | Usage analytics |
| `/token-usage` | `daily_token_usage.py` | Per-tenant daily token stats |
| `/billing` | `monthly_billing.py` | Monthly billing aggregation |
| `/batches` | `upload_batches.py` | Batch document upload tracking |
| `/api-keys` | `api_keys.py` | API key management |
| `/llm-models` | `llm_models.py` | LLM model registry |
| `/pipeline-status` | `pipeline_status.py` | Document processing pipeline status |

### Two authentication systems

| Who | How | Dependency |
|-----|-----|------------|
| Tenant admin users | JWT Bearer token | `get_current_user` in `app/utils/dependencies.py` |
| Chat consumers (end-users) | UUID header/query (`X-Chat-Consumer-UUID`) | `get_chat_consumer_from_uuid` in `app/utils/dependencies.py` |

API keys are issued per chatbot and scoped to a tenant. Chat consumers are anonymous sessions registered via `/api/v1/chat-auth/register`.

### RAG pipeline

Document upload → S3 (stored) + Celery task queued → worker runs `DocumentProcessingService` (chunking via `app/services/document_processing/chunker.py`) → embeddings via `OpenAIEmbeddingService` → vectors stored in Qdrant collection `{QDRANT_COLLECTION_PREFIX}_{tenant_id}`.

Query path: `conversations` endpoint → `ChatService` → `AgenticRAGService` (LangGraph agent with `AsyncPostgresSaver` checkpointing per `conversation_id` as `thread_id`) → vector similarity search → LLM response + citations.

### Services layer structure

- `app/services/agentic_rag/` — LangGraph agent (graph, nodes, tools, prompts, state)
- `app/services/document_processing/` — chunking, PDF/DOCX/image processing, factory
- `app/services/vector_store/` — abstraction over Qdrant/Chroma (factory pattern, `VECTOR_STORE_TYPE` env var selects)
- `app/services/llm/` — LLM provider abstraction (factory pattern, OpenAI provider)
- `app/services/embeddings/` — embedding service
- `app/services/web_scraper/` — web content extraction and Markdown conversion
- `app/services/observability/` — LangSmith tracing initialization
- `app/services/chatbot_config_service.py` — chatbot configuration management
- `app/services/token_tracking_service.py` — per-message token counting

### Multi-tenancy

Every chatbot, document, conversation, and vector collection is scoped to a `tenant_id`. The Qdrant collection name is `{QDRANT_COLLECTION_PREFIX}_{tenant_id}` (default prefix: `justedtech`). `DEFAULT_TENANT_ID=1` is the fallback tenant for seeded data.

### Celery tasks

- **Daily at 2 AM UTC**: `aggregate_daily_token_usage` — aggregates token usage + calculates costs per tenant/model
- **1st of month at 3 AM UTC**: `aggregate_monthly_billing` — rolls up daily data into monthly billing records
- **On-demand**: document ingestion tasks triggered on upload

Celery broker and backend both use Redis DB `/2` (separate from app Redis on DB `3`).

## Claude Code Customisations

Rules and skills for this project live in `.claude/`. They are automatically loaded by Claude Code.

### Rules (always-on — apply to every conversation)

| File | Covers |
|---|---|
| `.claude/rules/backend-core-rules.md` | Project layout, layered architecture, naming conventions, async-first, multi-tenancy |
| `.claude/rules/api-rules.md` | Endpoint pattern, Pydantic v2 schemas, dependency injection, `success_response()`, tenant isolation |
| `.claude/rules/database-rules.md` | SQLAlchemy async, CRUD pattern, Alembic migration workflow, `VectorStoreFactory` |
| `.claude/rules/code-quality-rules.md` | Ruff+Black+isort+mypy commands, type hints required, logging over `print()`, import order |
| `.claude/rules/gitops-rules.md` | Branching, commit format (`TICKET: summary`), PR body requirements, merge safety, stop conditions |

### Skills (on-demand — Claude activates when your request matches)

| Skill | Activate by asking to… |
|---|---|
| `add-api-endpoint` | Add a new route / create an endpoint / build a new domain end-to-end |
| `add-celery-task` | Run something in the background / add a scheduled or periodic job |
| `create-migration` | Add a model / change a column / create or apply an Alembic migration *(Bash+Read only)* |
| `code-review` | Review backend code / audit a change / self-review before merging *(read-only — no file edits)* |
| `github-pr-create` | Open a PR / push and raise a pull request on GitHub |
| `github-pr-review` | Review a PR / audit a pull request / check what a branch changes |

### Key env vars to set in `.env`

Required (not in `.env.example` defaults):
- `SECRET_KEY` — JWT signing key
- `OPENAI_API_KEY`
- `S3_BUCKET_NAME`, `S3_REGION`, `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`
- `BACKEND_CORS_ORIGINS` — JSON array of allowed origins

`VECTOR_STORE_TYPE=qdrant` is production default; `chroma` is available for local-only setups.
