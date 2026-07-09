---
description: Factory pattern for creating service instances
globs: app/services/**/factory.py
alwaysApply: false
---

# Factory Pattern

Use Factory pattern when you need to create instances of different implementations of the same interface.

## When to Use

- Multiple implementations of same interface (LLM providers, storage backends, processors)
- Runtime selection of implementation
- Need to add new implementations without modifying existing code

## Structure

```python
# app/services/llm/base.py
from abc import ABC, abstractmethod

class LLMProvider(ABC):
    """Base interface for LLM providers"""
    
    @abstractmethod
    def generate(self, prompt: str, **kwargs) -> str:
        """Generate response from prompt"""
        pass
    
    @abstractmethod
    def get_provider_name(self) -> str:
        """Return provider name"""
        pass

# app/services/llm/openai_provider.py
class OpenAIProvider(LLMProvider):
    def __init__(self, api_key: str, model: str = "gpt-4"):
        self.api_key = api_key
        self.model = model
    
    def generate(self, prompt: str, **kwargs) -> str:
        # OpenAI implementation
        pass
    
    def get_provider_name(self) -> str:
        return "openai"

# app/services/llm/factory.py
class LLMFactory:
    """Factory for creating LLM provider instances"""
    
    _providers: dict[str, type[LLMProvider]] = {
        "openai": OpenAIProvider,
        "anthropic": AnthropicProvider,
    }
    
    @classmethod
    def create(cls, provider_name: str, **kwargs) -> LLMProvider:
        """Create provider instance by name"""
        provider_class = cls._providers.get(provider_name)
        if not provider_class:
            raise ValueError(f"Unknown provider: {provider_name}")
        return provider_class(**kwargs)
    
    @classmethod
    def register(cls, name: str, provider_class: type[LLMProvider]) -> None:
        """Register new provider dynamically"""
        cls._providers[name] = provider_class
    
    @classmethod
    def get_available_providers(cls) -> list[str]:
        """Get list of registered providers"""
        return list(cls._providers.keys())
```

## Usage

```python
# In service or endpoint
llm = LLMFactory.create("openai", api_key=settings.OPENAI_API_KEY)
response = llm.generate("Hello, world!")

# Register custom provider
class CustomProvider(LLMProvider):
    pass

LLMFactory.register("custom", CustomProvider)
```

## Benefits

- Easy to add new implementations
- Centralized creation logic
- Runtime selection
- Open/Closed Principle
