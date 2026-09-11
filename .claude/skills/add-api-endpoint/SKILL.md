---
name: add-api-endpoint
description: Adds a new API endpoint to the FastAPI backend. Use when the user asks to add a new route, create a new endpoint, implement a new API, expose a new feature via HTTP, add a new resource to the REST API, or build a new domain from scratch.
---

When adding a new API endpoint, work bottom-up through the stack:

## 1. Model (if a new table is needed)

Create `app/models/{domain}.py` inheriting from `BaseModel`:

```python
from app.models.base import BaseModel
from sqlalchemy import Column, String, BigInteger, ForeignKey
from sqlalchemy.orm import relationship

class NewResource(BaseModel):
    __tablename__ = "new_resources"

    tenant_id = Column(BigInteger, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    name = Column(String, nullable=False)

    tenant = relationship("Tenant", back_populates="new_resources")
```

Export from `app/models/__init__.py`.

## 2. Migration

```bash
alembic revision --autogenerate -m "add_new_resources_table"
# Review the generated file in alembic/versions/ before applying
alembic upgrade head
```

## 3. Pydantic Schemas

Create `app/schemas/{domain}.py`:

```python
from pydantic import BaseModel, ConfigDict
from datetime import datetime

class NewResourceRequest(BaseModel):
    name: str

class NewResourceResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    tenant_id: int
    created_at: datetime
```

## 4. CRUD Class

Create `app/crud/{domain}.py` with a module-level singleton:

```python
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.models.{domain} import NewResource

class NewResourceCRUD:
    async def get(self, db: AsyncSession, resource_id: int) -> NewResource | None:
        result = await db.execute(select(NewResource).where(NewResource.id == resource_id))
        return result.scalar_one_or_none()

    async def list(self, db: AsyncSession, tenant_id: int) -> list[NewResource]:
        result = await db.execute(
            select(NewResource).where(NewResource.tenant_id == tenant_id)
        )
        return list(result.scalars().all())

    async def create(self, db: AsyncSession, data: NewResourceRequest, tenant_id: int) -> NewResource:
        obj = NewResource(**data.model_dump(), tenant_id=tenant_id)
        db.add(obj)
        await db.commit()
        return await self.get(db, obj.id)

new_resource = NewResourceCRUD()
```

## 5. Service Class

Create `app/services/{domain}.py` with a module-level singleton:

```python
import logging
from sqlalchemy.ext.asyncio import AsyncSession
from app.crud.{domain} import new_resource
from app.schemas.{domain} import NewResourceRequest, NewResourceResponse

logger = logging.getLogger(__name__)

class NewResourceService:
    async def create(self, db: AsyncSession, request: NewResourceRequest, tenant_id: int) -> NewResourceResponse:
        obj = await new_resource.create(db, request, tenant_id)
        return NewResourceResponse.model_validate(obj)

new_resource_service = NewResourceService()
```

## 5b. Error Handling in Service

Raise domain exceptions from `app/utils/exceptions.py` — never raw `HTTPException` in services:

```python
from app.utils.exceptions import NotFoundError, UnauthorizedError, ValidationError

class NewResourceService:
    async def get(self, db: AsyncSession, resource_id: int, tenant_id: int) -> NewResourceResponse:
        obj = await new_resource.get(db, resource_id)
        if not obj:
            raise NotFoundError("NewResource", resource_id)
        if obj.tenant_id != tenant_id:
            raise UnauthorizedError()
        return NewResourceResponse.model_validate(obj)

    async def create(self, db: AsyncSession, request: NewResourceRequest, tenant_id: int) -> NewResourceResponse:
        # Example business validation
        # if await new_resource.exists(db, name=request.name, tenant_id=tenant_id):
        #     raise ValidationError("A resource with this name already exists")
        obj = await new_resource.create(db, request, tenant_id)
        return NewResourceResponse.model_validate(obj)
```

The endpoint does nothing special — exception handlers in `main.py` catch and format the response automatically.

## 6. Endpoint

Create `app/api/endpoints/{domain}.py`:

```python
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from app.db.connector import get_db
from app.models.users import User
from app.utils.dependencies import get_current_user
from app.utils.response import success_response
from app.schemas.{domain} import NewResourceRequest
from app.services.{domain} import new_resource_service

router = APIRouter()

@router.get("/")
async def list_resources(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    result = await new_resource_service.list(db, current_user.tenant_id)
    return success_response(data=result)

@router.post("/")
async def create_resource(
    request: NewResourceRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    result = await new_resource_service.create(db, request, current_user.tenant_id)
    return success_response(data=result)
```

## 7. Register Router

Add to `app/api/api.py`:

```python
from app.api.endpoints import {domain}
api_router.include_router({domain}.router, prefix="/new-resources", tags=["NewResource"])
```

## 8. Quality Check

```bash
./quality_check.sh
```

## Constraints

- All methods must be `async def`
- Always return `success_response(data=...)` — never raw dicts
- Always inject `db` via `Depends(get_db)` — never instantiate session inline
- Always filter by `tenant_id` in CRUD queries
- Type hints required on all function signatures including return type (`-> ReturnType`)
- No `print()` — use `logging`
- Raise `NotFoundError` / `ValidationError` / `UnauthorizedError` from service layer — never `HTTPException` directly in services
- Use `@field_validator` (Pydantic v2) on schemas — never `@validator`
