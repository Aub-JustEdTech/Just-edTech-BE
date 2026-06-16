"""
RAG-specific schemas for query and response validation.
"""

from typing import Any

from pydantic import BaseModel


class RAGQuery(BaseModel):
    """Schema for RAG query requests"""

    query: str
    conversation_id: int | None = None
    max_tokens: int | None = 4000
    temperature: float | None = 0.7
    top_k: int | None = 5
    include_sources: bool = True


class DocumentChunk(BaseModel):
    """Schema for document chunks in RAG responses"""

    document_id: int
    document_title: str
    chunk_content: str
    similarity_score: float
    metadata: dict[str, Any] | None = None


class ImageResult(BaseModel):
    """Schema for image results in RAG responses"""

    image_url: str
    caption: str
    page_number: int | None = None
    similarity_score: float | None = None


class RAGResponse(BaseModel):
    """Schema for RAG response"""

    response: str
    sources: list[DocumentChunk] | None = []
    images: list[ImageResult] | None = []
    conversation_id: int | None = None
    tokens_used: int | None = None
    processing_time: float | None = None
