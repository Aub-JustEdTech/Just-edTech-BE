---
name: code-review
description: Reviews backend Python code for correctness, patterns, security, and quality. Use when the user asks to review code, check a PR, audit a change, inspect an endpoint or service, do a self-review before merging, or verify that new code follows project conventions.
allowed-tools: Read, Grep, Glob, Bash
---

When reviewing backend code, go through each category and report findings.

## 1. Architecture & Layering

- [ ] Endpoint handlers do not query the DB directly — all DB access goes via service or CRUD class
- [ ] Services contain business logic; CRUD classes contain only data access
- [ ] No business logic in models or schemas
- [ ] New domain follows the correct file structure: `endpoints/`, `schemas/`, `services/`, `crud/`, `models/`

## 2. Async Correctness

- [ ] All I/O operations are `async def` — DB, Redis, S3, email, external HTTP
- [ ] All `await` calls are present where needed (no missing `await` on coroutines)
- [ ] No `time.sleep()` — must be `await asyncio.sleep()`
- [ ] No synchronous blocking calls inside async functions (e.g., `requests.get` instead of `httpx.AsyncClient`)

## 3. API / Endpoints

- [ ] Router registered in `app/api/api.py`
- [ ] `db: AsyncSession = Depends(get_db)` — session injected, not instantiated inline
- [ ] Auth dependency used from the correct set — never verified inline:
  - JWT users: `get_current_user`, `get_current_tenant_admin`, `get_current_super_admin`
  - Chat consumers: `get_chat_consumer_from_uuid` (UUID via `X-Chat-Consumer-UUID` header or query param)
  - Hybrid: `get_current_user_or_chat_consumer`, `get_principal_with_api_key` (API key + principal, tenant-enforced)
  - Tenant isolation: `require_tenant_access()`
- [ ] All responses use `success_response(data=...)` — no raw dicts or Pydantic models returned directly
- [ ] Tenant isolation enforced (`require_tenant_access()` dependency or `tenant_id` filter in CRUD)
- [ ] Services raise domain exceptions (`NotFoundError`, `ValidationError`, `UnauthorizedError`) — not raw `HTTPException`
- [ ] Exception handlers registered in `main.py` via `register_exception_handlers(app)`

## 4. Pydantic Schemas

- [ ] Pydantic v2 style: `model_config = ConfigDict(from_attributes=True)` for ORM conversion
- [ ] Separate request and response schemas — no schema used for both
- [ ] Naming: `{Resource}Request`, `{Resource}Response`
- [ ] Validators use `@field_validator` (Pydantic v2), not `@validator` (Pydantic v1)
- [ ] Base schemas used for shared fields where appropriate (`UserBase` → `UserCreate`, `UserResponse`)
- [ ] `Field(...)` constraints used for string length / numeric range where applicable

## 5. CRUD & Database

- [ ] `select()` used for queries — no raw SQL strings
- [ ] `selectinload()` used for relationship loading — no lazy loading in async context
- [ ] `scalar_one_or_none()` used for single-row fetches
- [ ] `tenant_id` filtered in all multi-tenant queries
- [ ] Module-level singleton instance present: `{resource} = {Resource}CRUD()`
- [ ] No N+1 query patterns (loading relationships inside a loop)

## 6. Migrations

- [ ] New model exported from `app/models/__init__.py`
- [ ] Migration created with `--autogenerate` (not handwritten SQL)
- [ ] `downgrade()` is implemented (not `pass`)
- [ ] No modification of existing migration files

## 7. Celery Tasks

- [ ] `bind=True` and `max_retries=3` on tasks that can fail
- [ ] Exponential backoff: `countdown=60 * (2 ** self.request.retries)`
- [ ] Async code bridged via `loop_utils.get_event_loop()`
- [ ] New `AsyncSessionLocal()` opened inside async impl (not passed from outside)
- [ ] Task imported in `app/tasks/__init__.py` for Celery discovery
- [ ] Periodic tasks registered in beat schedule in `app/celery_app.py`

## 8. Code Quality

- [ ] Type hints on all function signatures
- [ ] Return type annotated on all functions — not just parameters (`-> ReturnType`)
- [ ] `| None` used for optional returns — not `Optional[X]` unless targeting < Python 3.10
- [ ] No `print()` — `logging` used instead
- [ ] Import order: stdlib → third-party → local (`app.*`), separated by blank lines
- [ ] No unused imports (Ruff F401)
- [ ] Line length ≤ 88 characters
- [ ] `./quality_check.sh` would pass

## 9. Security

- [ ] No secrets, tokens, or API keys hardcoded — all from `settings` (Pydantic BaseSettings)
- [ ] User input validated via Pydantic schemas before use — no raw request body access
- [ ] SQL injection impossible — ORM (`select()`) used everywhere, no string interpolation in queries
- [ ] No `dangerouslySetInnerHTML` equivalent — no `eval()` or `exec()` on user input
- [ ] Passwords hashed with `get_password_hash()` — never stored in plaintext

## 10. Multi-Tenancy

- [ ] Every new model has `tenant_id` column with FK to `tenants.id`
- [ ] CRUD queries filter by `tenant_id`
- [ ] No cross-tenant data leakage possible in list endpoints

## 11. Factory & Strategy Patterns

- [ ] If a factory is present: new implementations registered in `_providers` dict — never instantiated directly in callers
- [ ] Factory `create()` guard preserved — raises `ValueError` for unknown names
- [ ] If a strategy is present: `can_handle()` does not overlap with other strategies
- [ ] `ProcessorManager` (or equivalent context) not modified to add type-specific conditional logic
- [ ] Factory/Strategy base classes are ABCs with `@abstractmethod` on all interface methods

---

Report findings grouped by category. For each issue: file path, what the problem is, and how to fix it. If a category has no issues, mark it ✓.
