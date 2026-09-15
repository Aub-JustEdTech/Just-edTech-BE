# Backend Core Rules

## Project Layout

```
app/
  api/endpoints/   ← Route handlers (one file per domain)
  schemas/         ← Pydantic request/response models
  services/        ← Business logic (orchestrates CRUD + external APIs)
  crud/            ← Data access layer (one class per model)
  models/          ← SQLAlchemy ORM models
  tasks/           ← Celery background tasks
  core/            ← Settings (config.py)
  db/              ← Database/Redis connection setup
  utils/           ← Auth, dependencies, response helpers, S3, email
  main.py          ← FastAPI app entry point and lifespan
```

## Layered Architecture

**Request flow:** HTTP → Router (`api/api.py`) → Endpoint (`api/endpoints/`) → Service (`services/`) → CRUD (`crud/`) → Model/DB

- Endpoints must never query the database directly — always via a service or CRUD class
- Services orchestrate CRUD operations and external calls (OpenAI, S3, Redis, email)
- CRUD classes only do data access — no business logic

## Naming Conventions

| Artifact | Convention | Example |
|---|---|---|
| Files/modules | `snake_case` | `document_service.py`, `user_crud.py` |
| Classes | `PascalCase` | `DocumentService`, `UserCRUD` |
| Functions/methods | `snake_case` | `get_document`, `process_chunk` |
| Constants/Enums | `UPPER_CASE` | `ProcessingStatus`, `MAX_RETRIES` |
| Service instances | module-level singleton | `document_service = DocumentService()` |
| CRUD instances | module-level singleton | `document = DocumentCRUD()` |
| FK columns | `{relation}_id` | `tenant_id`, `user_id`, `chatbot_id` |
| Boolean flags | `is_` or `enable_` prefix | `is_active`, `enable_multimodal` |
| Status fields | PostgreSQL ENUM via SQLAlchemy | `ProcessingStatus`, `AuthStage` |

## Async-First

All I/O operations must be `async def`:
- Database: `AsyncSession` with `await db.execute(...)`
- Redis: `await redis_manager.get(...)`
- S3: `aioboto3` async client
- Email: async SMTP
- External HTTP: `httpx.AsyncClient`

Never use `time.sleep()` — use `await asyncio.sleep()`. Never use synchronous DB calls inside async endpoints.

## Dependency Management

- Use `poetry add <package>` — never `pip install`
- Commit `poetry.lock` with every dependency change
- Dev-only dependencies: `poetry add --group dev <package>`

## Multi-Tenancy

Every resource model must have `tenant_id`. Tenant isolation must be enforced:
- In CRUD `.where()` clauses: `where(Model.tenant_id == tenant_id)`
- In FastAPI dependencies: use `require_tenant_access()` for tenant-scoped routes
- Qdrant collections are named `{QDRANT_COLLECTION_PREFIX}_{tenant_id}` (default prefix: `justedtech`)

## Code Quality Before Committing

No pre-commit hooks are configured — run manually:
```bash
./quality_check.sh
```
