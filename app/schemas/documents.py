"""
Document schemas for request/response validation.
"""

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, HttpUrl, TypeAdapter, field_validator

from app.models.documents import ProcessingStatus

# Create TypeAdapter once at module level for efficiency (Pydantic v2)
_http_url_adapter = TypeAdapter(HttpUrl)


class DocumentSortField(str, Enum):
    """Sortable fields for document list."""

    NAME = "name"
    CREATED_AT = "created_at"
    UPDATED_AT = "updated_at"
    FILE_SIZE_BYTES = "file_size_bytes"
    CHUNK_COUNT = "chunk_count"
    DOCUMENT_TYPE = "document_type"
    PROCESSING_STATUS = "processing_status"


class SortOrder(str, Enum):
    """Sort order options."""

    ASC = "asc"
    DESC = "desc"


class DocumentUploadResponse(BaseModel):
    """Response for document upload"""

    id: int
    name: str
    doc_id: str
    document_type: str
    processing_status: ProcessingStatus
    file_size_bytes: int | None
    tenant_id: int
    created_at: datetime

    class Config:
        from_attributes = True


class DocumentScrapeRequest(BaseModel):
    """Request for scraping a web page"""

    url: str = Field(..., description="URL to scrape (must be valid HTTP/HTTPS URL)")
    name: str | None = Field(
        None,
        description="Label/name from frontend (if provided, used as filename; otherwise uses page title or URL-based name)",
    )
    include_metadata: bool = Field(
        True, description="Include page metadata (title, description, author)"
    )
    timeout_seconds: int = Field(
        30, ge=1, le=120, description="Request timeout in seconds (max: 120)"
    )
    verify_ssl: bool = Field(
        True,
        description="Verify SSL certificates; set false only for trusted sources with bad certs.",
    )

    @field_validator("url")
    @classmethod
    def validate_url(cls, v: str) -> str:
        """Validate URL format"""
        if not v or not isinstance(v, str):
            raise ValueError("URL must be a non-empty string")
        v = v.strip()
        # Add protocol if missing
        if not v.startswith(("http://", "https://")):
            v = f"https://{v}"
        # Validate using Pydantic's HttpUrl with TypeAdapter (Pydantic v2)
        try:
            _http_url_adapter.validate_python(v)
        except Exception as e:
            raise ValueError(f"Invalid URL format: {v}") from e
        return v


class DocumentScrapeResponse(DocumentUploadResponse):
    """Response for document scraping with additional metadata"""

    source_url: str = Field(..., description="Original URL that was scraped")
    scraped_at: datetime = Field(..., description="Timestamp when the page was scraped")
    content_length: int = Field(
        ..., description="Length of extracted markdown content in bytes"
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Extracted page metadata (title, description, etc.)",
    )


class DocumentListResponse(BaseModel):
    """Response for document list"""

    id: int
    name: str
    doc_id: str
    document_type: str
    processing_status: ProcessingStatus
    chunk_count: int
    file_size_bytes: int | None
    error_message: str | None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class DocumentDetailResponse(DocumentListResponse):
    """Response for document details with full information"""

    s3_url: str | None
    tenant_id: int

    class Config:
        from_attributes = True


class DocumentSearchRequest(BaseModel):
    """Request for document search"""

    query: str = Field(..., min_length=1, description="Search query")
    limit: int = Field(5, ge=1, le=50, description="Number of results to return")
    document_types: list[str] | None = Field(
        None, description="Filter by document types"
    )
    document_ids: list[str] | None = Field(
        None, description="Filter by specific document IDs"
    )


class SearchResult(BaseModel):
    """Single search result"""

    chunk_id: str
    text: str
    document_id: str
    document_name: str
    chunk_index: int
    distance: float
    metadata: dict[str, Any]


class DocumentSearchResponse(BaseModel):
    """Response for document search"""

    query: str
    results: list[SearchResult]
    total_results: int


class PresignedUrlResponse(BaseModel):
    """Response containing a presigned URL for a document in S3"""

    url: str
    expires_in: int


class DocumentBulkDeleteRequest(BaseModel):
    """Request payload for bulk document deletion"""

    document_ids: list[int] = Field(
        ...,
        min_length=1,
        description="List of document IDs to delete",
    )


class DocumentBulkDeleteFailure(BaseModel):
    """Failure details for bulk document deletion"""

    document_id: int
    reason: str


class DocumentBulkDeleteResponse(BaseModel):
    """Response payload for bulk document deletion"""

    deleted_document_ids: list[int] = Field(
        default_factory=list, description="Document IDs successfully deleted"
    )
    failed_documents: list[DocumentBulkDeleteFailure] = Field(
        default_factory=list, description="Documents that failed to delete with reasons"
    )


class ProcessingJobResponse(BaseModel):
    """Response for processing job status"""

    id: int
    document_id: int
    status: str
    processor_type: str | None
    error_message: str | None
    chunks_created: int
    processing_time_seconds: float | None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


# Legacy schemas for backward compatibility
class DocumentBase(BaseModel):
    """Base document schema"""

    title: str
    content: str
    file_type: str | None = None
    metadata: dict[str, Any] | None = None


class DocumentCreate(DocumentBase):
    """Schema for document creation"""

    pass


class DocumentUpdate(BaseModel):
    """Schema for document updates"""

    title: str | None = None
    content: str | None = None
    file_type: str | None = None
    metadata: dict[str, Any] | None = None


class DocumentInDB(DocumentBase):
    """Schema for document in database"""

    id: int
    file_path: str | None = None
    file_size: int | None = None
    embedding_model: str | None = None
    chunk_count: int = 0
    processing_status: str = "pending"
    owner_id: int

    class Config:
        from_attributes = True


class Document(DocumentBase):
    """Schema for document response"""

    id: int
    file_path: str | None = None
    file_size: int | None = None
    chunk_count: int = 0
    processing_status: str = "pending"
    owner_id: int

    class Config:
        from_attributes = True
