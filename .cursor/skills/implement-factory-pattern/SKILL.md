---
name: implement-factory-pattern
description: Adds a new provider or implementation to an existing factory, or creates a new factory from scratch. Use when the user asks to add a new LLM provider, storage backend, vector store implementation, or any interchangeable service behind a factory.
allowed-tools: Read, Edit, Write, Bash, Grep, Glob
---

When implementing a factory pattern, follow these steps in order.

## Step 1 — Find or Define the ABC Base

First check if a base class already exists:

```bash
grep -r "abstractmethod" app/services/ --include="*.py" -l
```

If a base exists, read it to understand the required interface (`@abstractmethod` methods).

If no base exists, create `app/services/{domain}/base.py`:

```python
from abc import ABC, abstractmethod
from typing import Any


class {Domain}Provider(ABC):
    """Abstract base for {domain} provider implementations."""

    @abstractmethod
    def can_handle(self, input_type: str) -> bool:
        """Return True if this provider handles the given type."""

    @abstractmethod
    async def execute(self, data: Any, **kwargs: Any) -> Any:
        """Execute the provider's core operation."""

    @abstractmethod
    def get_provider_name(self) -> str:
        """Return the unique name used to register this provider."""
```

Adapt method signatures to match the domain — do not change existing ABCs.

## Step 2 — Create the Concrete Implementation

Create `app/services/{domain}/{impl_name}_provider.py`:

```python
import logging
from typing import Any

from app.services.{domain}.base import {Domain}Provider

logger = logging.getLogger(__name__)


class {ImplName}Provider({Domain}Provider):
    """Implementation of {Domain}Provider using {ImplName}."""

    def __init__(self, **kwargs: Any) -> None:
        # Store config from kwargs — read from settings, not hardcoded
        pass

    def can_handle(self, input_type: str) -> bool:
        return input_type == "{impl_name}"

    async def execute(self, data: Any, **kwargs: Any) -> Any:
        logger.info("{ImplName}Provider executing for type %s", type(data).__name__)
        # implementation here
        pass

    def get_provider_name(self) -> str:
        return "{impl_name}"
```

Rules:
- Pull config from `settings` (Pydantic BaseSettings) — never hardcode API keys
- Use `async` for any I/O operations (HTTP, file, DB)

## Step 3 — Register in the Factory

Find the factory file:

```bash
find app/services/{domain} -name "factory.py"
```

Read the factory file, then add the new provider to `_providers`:

```python
# app/services/{domain}/factory.py
from app.services.{domain}.{impl_name}_provider import {ImplName}Provider

class {Domain}Factory:
    _providers: dict[str, type[{Domain}Provider]] = {
        "existing_impl": ExistingProvider,
        "{impl_name}": {ImplName}Provider,  # ← add this line only
    }

    @classmethod
    def create(cls, provider_name: str, **kwargs: Any) -> {Domain}Provider:
        provider_class = cls._providers.get(provider_name)
        if not provider_class:
            raise ValueError(f"Unknown provider: {provider_name}. Available: {list(cls._providers)}")
        return provider_class(**kwargs)

    @classmethod
    def register(cls, name: str, provider_class: type[{Domain}Provider]) -> None:
        """Register a provider dynamically (for plugins/testing)."""
        cls._providers[name] = provider_class
```

If the factory does not yet have `register()`, add it.

## Step 4 — Wire Usage in Callers

Callers use `Factory.create(name, **kwargs)` — never instantiate providers directly:

```python
# ✅ Correct
from app.services.{domain}.factory import {Domain}Factory

provider = {Domain}Factory.create(
    settings.{DOMAIN}_PROVIDER,
    api_key=settings.{DOMAIN}_API_KEY,
)
result = await provider.execute(data)

# ❌ Wrong — bypasses factory
from app.services.{domain}.openai_provider import OpenAIProvider
provider = OpenAIProvider(api_key=settings.OPENAI_API_KEY)
```

## Step 5 — Quality Check

```bash
./quality_check.sh
```

## Constraints

- Add exactly one entry to `_providers` — do not touch other providers
- The `create()` guard (`ValueError` for unknown names) must be preserved — do not remove it
- Factory is the only legal instantiation path for providers
- Open/Closed: extend by adding to `_providers`, never by branching inside `create()`
- New provider must implement every `@abstractmethod` — Python will raise `TypeError` at import if not
- Config/credentials come from `settings` only — no hardcoded strings
