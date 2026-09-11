---
description: Strategy pattern for interchangeable algorithms
globs: app/services/**/base.py
alwaysApply: false
---

# Strategy Pattern

Use Strategy pattern when you have multiple algorithms/implementations for the same task.

## When to Use

- Multiple ways to process the same type of data
- Algorithm selection at runtime
- Need to add new algorithms without modifying existing code

## Structure

```python
# app/services/processors/base.py
from abc import ABC, abstractmethod

class Processor(ABC):
    """Base strategy interface"""
    
    @abstractmethod
    def can_handle(self, input_type: str) -> bool:
        """Check if this processor can handle the input"""
        pass
    
    @abstractmethod
    def process(self, input_data: Any) -> Any:
        """Process the input data"""
        pass

# app/services/processors/pdf_processor.py
class PDFProcessor(Processor):
    def can_handle(self, input_type: str) -> bool:
        return input_type == "pdf"
    
    def process(self, input_data: bytes) -> str:
        # PDF processing logic
        return extracted_text

# app/services/processors/docx_processor.py
class DOCXProcessor(Processor):
    def can_handle(self, input_type: str) -> bool:
        return input_type in ["docx", "doc"]
    
    def process(self, input_data: bytes) -> str:
        # DOCX processing logic
        return extracted_text

# app/services/processors/manager.py
class ProcessorManager:
    """Context that uses strategies"""
    
    def __init__(self):
        self.processors: list[Processor] = [
            PDFProcessor(),
            DOCXProcessor(),
        ]
    
    def process(self, input_data: Any, input_type: str) -> Any:
        """Select and use appropriate processor"""
        for processor in self.processors:
            if processor.can_handle(input_type):
                return processor.process(input_data)
        raise ValueError(f"No processor for type: {input_type}")
    
    def register(self, processor: Processor) -> None:
        """Add new processor dynamically"""
        self.processors.append(processor)
```

## Benefits

- Open/Closed Principle (open for extension, closed for modification)
- Each strategy independently testable
- Easy to add new strategies
- Runtime algorithm selection
