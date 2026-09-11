---
name: implement-strategy-pattern
description: Adds a new processor, handler, or algorithm variant to an existing strategy-based system. Use when the user asks to add a new document processor, file type handler, chunker, embedder, or any new implementation of an existing strategy interface.
allowed-tools: Read, Edit, Write, Bash, Grep, Glob
---

When implementing a new strategy, follow these steps in order.

## Step 1 — Find the ABC Base

Locate the existing base class and manager:

```bash
grep -r "abstractmethod" app/services/ --include="*.py" -l
grep -r "can_handle\|ProcessorManager\|process(" app/services/ --include="*.py" -l
```

Read the base file to understand the required interface — specifically the signatures of `can_handle()` and `process()` (or the equivalent abstract methods).

## Step 2 — Audit Existing Strategies

List all existing strategies to ensure the new `can_handle()` does not overlap:

```bash
grep -r "def can_handle" app/services/ --include="*.py" -A 2
```

Note every input type already claimed. The new strategy must not return `True` for any of those types.

## Step 3 — Create the New Strategy

Create `app/services/processors/{name}_processor.py` (or the equivalent path for the domain):

```python
import logging
from typing import Any

from app.services.processors.base import Processor

logger = logging.getLogger(__name__)


class {Name}Processor(Processor):
    """Handles {name} input type."""

    def can_handle(self, input_type: str) -> bool:
        return input_type in ["{name}", "{name_alias}"]  # no overlap with existing

    def process(self, input_data: Any) -> Any:
        logger.info("{Name}Processor processing input")
        # implementation here
        result = ...
        return result
```

Rules:
- Match exactly the abstract method signatures from the base — name, parameters, return type
- Use `async def` if the base methods are async
- Use `logging`, never `print()`
- Type hints required on all methods

## Step 4 — Register in the Manager

Find the manager file:

```bash
grep -r "self.processors\|ProcessorManager" app/services/ --include="*.py" -l
```

Read the manager's `__init__` and append the new processor — one line only:

```python
# app/services/processors/manager.py
class ProcessorManager:
    def __init__(self) -> None:
        self.processors: list[Processor] = [
            PDFProcessor(),
            DOCXProcessor(),
            {Name}Processor(),  # ← add this line only
        ]
```

Do NOT modify `process()` or any other method in the manager.

## Step 5 — Verify Independently

Each strategy is independently testable without the manager. Confirm the new class can be exercised directly:

```python
processor = {Name}Processor()
assert processor.can_handle("{name}") is True
assert processor.can_handle("pdf") is False  # no overlap
result = processor.process(test_input)
assert result is not None
```

## Step 6 — Quality Check

```bash
./quality_check.sh
```

## Constraints

- `can_handle()` must not return `True` for any type already handled by an existing strategy — check step 2 first
- Never add `if input_type == "{name}"` branches inside `ProcessorManager.process()` — that defeats the pattern
- `ProcessorManager` code changes are limited to one line (the registration) — no other modifications
- Open/Closed: extend by adding a new class, never by modifying existing ones
- The new class must implement every `@abstractmethod` from the base — Python raises `TypeError` at import if any are missing
- Module-level instantiation is fine for stateless processors — add `{name}_processor = {Name}Processor()` singleton if the codebase uses that pattern
