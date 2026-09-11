# Database Rules

## SQLAlchemy Models

All models inherit from the custom `BaseModel` in `app/models/base.py` (provides `id`, `created_at`, `updated_at`):

```python
from app.models.base import BaseModel
from sqlalchemy import Column, String, BigInteger, ForeignKey, Boolean
from sqlalchemy.orm import relationship

class Document(BaseModel):
    __tablename__ = "documents"

    tenant_id = Column(BigInteger, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    name = Column(String, nullable=False)
    is_active = Column(Boolean, default=True)
    processing_status = Column(SQLAlchemyEnum(ProcessingStatus), default=ProcessingStatus.PENDING)

    tenant = relationship("Tenant", back_populates="documents")
```

Model conventions:
- FK columns: `ondelete="CASCADE"` or `ondelete="SET NULL"`
- Boolean flags: `is_` or `enable_` prefix
- Timestamps: always UTC (handled by `BaseModel`)
- Status fields: PostgreSQL ENUM via `SQLAlchemyEnum`
- Always define `__tablename__` explicitly

## Session Usage

Only use `AsyncSession` from `get_db()` — never create a session inline:

```python
# ✅ Correct
async def get_document(db: AsyncSession, document_id: int) -> Document | None:
    result = await db.execute(select(Document).where(Document.id == document_id))
    return result.scalar_one_or_none()

# ❌ Never do this
session = AsyncSessionLocal()  # don't instantiate directly
```

## CRUD Pattern

One `{Resource}CRUD` class per model in `app/crud/{domain}.py`, with a module-level singleton:

```python
class DocumentCRUD:
    async def get(self, db: AsyncSession, document_id: int) -> Document | None:
        result = await db.execute(
            select(Document)
            .options(selectinload(Document.tenant))
            .where(Document.id == document_id)
        )
        return result.scalar_one_or_none()

    async def list(self, db: AsyncSession, tenant_id: int) -> list[Document]:
        result = await db.execute(
            select(Document).where(Document.tenant_id == tenant_id)
        )
        return list(result.scalars().all())

    async def create(self, db: AsyncSession, data: DocumentCreate) -> Document:
        db_doc = Document(**data.model_dump())
        db.add(db_doc)
        await db.commit()
        return await self.get(db, db_doc.id)

    async def update(self, db: AsyncSession, document: Document, updates: dict) -> Document:
        for key, value in updates.items():
            setattr(document, key, value)
        await db.commit()
        await db.refresh(document)
        return document

    async def delete(self, db: AsyncSession, document: Document) -> None:
        await db.delete(document)
        await db.commit()

document = DocumentCRUD()  # singleton — import and use this instance everywhere
```

Query rules:
- Use `select()` with `.where()` — never raw SQL strings
- Use `selectinload()` for eager relationship loading (avoids N+1 queries)
- Use `scalar_one_or_none()` for single-row fetches
- Always filter by `tenant_id` in multi-tenant queries

## Alembic Migrations

```bash
# Create (always autogenerate — never write migration SQL by hand)
alembic revision --autogenerate -m "add_document_status_column"

# ALWAYS review the generated file in alembic/versions/ before applying
# Verify upgrade() and downgrade() are correct

# Apply
alembic upgrade head

# Rollback one step
alembic downgrade -1
```

Rules:
- Never modify an existing migration file — create a new one
- Never create or drop tables with raw SQL — always use Alembic
- Migration description must be short and descriptive (snake_case)
- Always implement `downgrade()` — don't leave it as `pass`

## Vector Store

Qdrant collections are named `{QDRANT_COLLECTION_PREFIX}_{tenant_id}` (default prefix: `justedtech`).

Always access via `VectorStoreFactory` — never import and use the Qdrant client directly:

```python
# ✅ Correct
from app.services.vector_store.factory import VectorStoreFactory
vector_store = VectorStoreFactory.get_instance()

# ❌ Never do this
from qdrant_client import QdrantClient
client = QdrantClient(...)
```
