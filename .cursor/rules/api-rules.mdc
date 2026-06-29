# API Rules

## Endpoint Pattern

Each domain gets its own file in `app/api/endpoints/{domain}.py`:

```python
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from app.db.connector import get_db
from app.utils.dependencies import get_current_user
from app.utils.response import success_response
from app.schemas.{domain} import {Resource}Request, {Resource}Response

router = APIRouter()

@router.post("/", response_model=None)
async def create_resource(
    request: {Resource}Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    result = await {domain}_service.create(db, request, current_user.tenant_id)
    return success_response(data=result)
```

Register in `app/api/api.py`:
```python
api_router.include_router({domain}.router, prefix="/{domain}s", tags=["{Domain}"])
```

## Schema Pattern (Pydantic v2)

Separate request and response schemas in `app/schemas/{domain}.py`:

```python
from pydantic import BaseModel, ConfigDict

class {Resource}Request(BaseModel):
    name: str
    # request fields

class {Resource}Response(BaseModel):
    model_config = ConfigDict(from_attributes=True)  # enables ORM → Pydantic conversion

    id: int
    name: str
    created_at: datetime
```

Naming: `{Resource}Request`, `{Action}Request`, `{Resource}Response`

## Dependency Injection

Always inject via `Depends()` — never instantiate sessions or verify tokens inline:

```python
db: AsyncSession = Depends(get_db)                          # DB session
current_user: User = Depends(get_current_user)              # JWT tenant admin
current_user: User = Depends(get_current_tenant_admin)      # tenant admin role check
current_user: User = Depends(get_current_super_admin)       # super admin role check
consumer = Depends(get_chat_consumer_from_uuid)             # anonymous chat consumer (UUID header/query)
principal = Depends(get_current_user_or_chat_consumer)      # hybrid: user OR chat consumer
principal = Depends(get_principal_with_api_key)             # API key + (user OR consumer) — tenant-enforced
_ = Depends(require_api_key)                                # API key header only

# Module-level aliases (importable from app.utils.dependencies):
# require_super_admin, require_tenant_admin, require_chat_consumer
# require_user_or_chat_consumer, require_api_key_user_or_chat_consumer
```

## Standardised Response Format

Always return `success_response()` — never raw dicts or Pydantic models directly:

```python
from app.utils.response import success_response

return success_response(data=result)
return success_response(data=results, extra={"page": page, "total": total})
```

Response envelope:
```json
{
  "success": true,
  "data": { ... },
  "error": null,
  "extra": { "page": 1, "total": 100 }
}
```

## Route Naming

- RESTful plural nouns: `/documents`, `/conversations`, `/chatbots`
- All routes prefixed with `/api/v1` (set in `api.py` → `main.py`)
- Use HTTP verbs correctly: `GET` for reads, `POST` for creates, `PUT`/`PATCH` for updates, `DELETE` for deletes

## Tenant Isolation

Routes that access tenant-scoped resources must use `require_tenant_access()`:

```python
@router.get("/{tenant_id}/resources")
async def list_resources(
    tenant_id: int,
    current_user: User = Depends(require_tenant_access()),
    db: AsyncSession = Depends(get_db),
):
    ...
```

Never expose one tenant's data to another — enforce `tenant_id` in every CRUD query.
