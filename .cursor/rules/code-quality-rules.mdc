# Code Quality Rules

## Tools

| Tool | Purpose | Config |
|---|---|---|
| Ruff | Linting + formatting | `pyproject.toml` `[tool.ruff]` |
| Black | Code formatting | `pyproject.toml` `[tool.black]` |
| isort | Import sorting | `pyproject.toml` `[tool.isort]` |
| mypy | Static type checking | `pyproject.toml` `[tool.mypy]` |
| pytest | Testing | `pyproject.toml` `[tool.pytest]` |

## Run All Checks

```bash
./quality_check.sh
```

## Individual Commands

```bash
poetry run ruff check app/ --fix    # lint with auto-fix
poetry run ruff format app/         # format with Ruff
poetry run black .                  # format with Black
poetry run isort .                  # sort imports
poetry run mypy app/                # type check
poetry run pytest tests/            # run tests
poetry run pytest tests/ -k "test_name"  # run single test
```

## Ruff Config (pyproject.toml)

- Target: Python 3.12
- Line length: 88
- Rules enforced: E, W, F, I, B, C4, UP
- Excludes: `alembic/`, `.git`, `chroma_db`, `build`, `dist`
- Per-file ignore: `F401` (unused imports) in `__init__.py` files

## Type Hints

Type hints are required on all function signatures:

```python
# ✅ Correct
async def get_document(db: AsyncSession, document_id: int) -> Document | None:
    ...

# ❌ Missing return type and parameter types
async def get_document(db, document_id):
    ...
```

Use `from __future__ import annotations` at the top of files with forward references.

## Logging

Never use `print()` in committed code — use the `logging` module:

```python
import logging

logger = logging.getLogger(__name__)

logger.info("Processing document %s", document_id)
logger.error("Failed to process document %s: %s", document_id, str(exc))
```

## Import Order

isort and Ruff enforce this order (separate each group with a blank line):

```python
# 1. Standard library
from __future__ import annotations
import logging
from datetime import datetime

# 2. Third-party
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

# 3. Local (app.*)
from app.crud.documents import document
from app.utils.response import success_response
```

## Async Testing

```python
import pytest
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)

# Sync test
def test_health_check():
    response = client.get("/health")
    assert response.status_code == 200

# Async test
@pytest.mark.asyncio
async def test_async_operation():
    result = await some_async_function()
    assert result is not None
```

Test files go in `tests/`. Use `pytest-asyncio` for async tests.
