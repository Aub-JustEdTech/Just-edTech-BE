# Document Ingestion API Documentation

## Overview

The Document Ingestion feature allows uploading, processing, and semantic search of documents (PDF, Markdown, TXT, DOCX) with automatic embedding generation and vector storage.

## Architecture Components

### 1. **API Endpoints** (`app/api/endpoints/documents.py`)
- Document upload
- List documents
- Get document details
- Delete documents
- Semantic search
- Processing job status
- Reprocess documents

### 2. **Database Models**
- `Document` - Stores document metadata and status
- `DocumentProcessingJob` - Tracks processing job history
- `LLMModel` - Stores embedding and chat model configurations
- `TenantConfig` - Per-tenant chunking and model settings

### 3. **Services**
- **DocumentService** - Main orchestrator
- **EmbeddingService** - Generates embeddings using OpenAI
- **VectorStore** - Stores and searches embeddings (ChromaDB default)
- **DocumentProcessors** - Extracts text from different file types
- **Chunker** - Splits text into chunks
- **S3Manager** - Handles file storage

## API Endpoints

### 1. Upload Document

```http
POST /api/documents/upload
Content-Type: multipart/form-data
Authorization: Bearer <token>

file: <binary file>
```

**Supported File Types:** `.pdf`, `.md`, `.txt`, `.docx`  
**Max File Size:** 50MB (configurable)

**Response:**
```json
{
  "id": 123,
  "name": "example.pdf",
  "doc_id": "uuid-here",
  "document_type": ".pdf",
  "processing_status": "pending",
  "file_size_bytes": 1024000,
  "tenant_id": 1,
  "created_at": "2025-10-06T18:00:00Z"
}
```

**Status Codes:**
- `201` - Document uploaded successfully
- `400` - Invalid file type or size
- `413` - File too large
- `500` - Upload failed

---

### 2. List Documents

```http
GET /api/documents?skip=0&limit=100&document_type=.pdf&processing_status=completed&search=report&sort_by=created_at&sort_order=desc
Authorization: Bearer <token>
```

**Query Parameters:**
- `skip` (int, default: 0) - Pagination offset
- `limit` (int, default: 100, max: 500) - Number of results
- `document_type` (string, optional) - Filter by type (`.pdf`, `.md`, `.txt`, `.docx`)
- `processing_status` (enum, optional) - Filter by status (`pending`, `processing`, `completed`, `failed`)
- `include_failed` (bool, default: `false`) - When no `processing_status` is set, the default list **excludes** failed documents so the main view shows only usable docs. Set to `true` to include failed docs (e.g. for a "Failed uploads" tab).
- `search` (string, optional) - Case-insensitive substring match on document name
- `sort_by` (enum, optional, default: `created_at`) - Sort field (`name`, `created_at`, `updated_at`, `file_size_bytes`, `chunk_count`, `document_type`, `processing_status`)
- `sort_order` (enum, optional, default: `desc`) - Sort direction (`asc`, `desc`)

**Response:**
```json
[
  {
    "id": 123,
    "name": "example.pdf",
    "doc_id": "uuid-here",
    "document_type": ".pdf",
    "processing_status": "completed",
    "chunk_count": 45,
    "file_size_bytes": 1024000,
    "error_message": null,
    "created_at": "2025-10-06T18:00:00Z",
    "updated_at": "2025-10-06T18:05:00Z"
  }
]
```

**Handling failed documents**
- When processing fails after upload, the document remains in the DB with `processing_status: "failed"` and `error_message` set (e.g. "Failed at chunking: ...").
- The default list call (no `processing_status`, `include_failed=false`) **excludes** failed docs, so users see only pending/processing/completed.
- To show failed uploads: call list with `include_failed=true` or `processing_status=failed`. Display `error_message` and offer **Reprocess** via `POST /api/v1/documents/{document_id}/reprocess` (no re-upload needed).
- Re-uploading the same file creates a **new** document; there is no automatic "same file" detection. Use reprocess for retries.

---

### 3. Get Document Details

```http
GET /api/documents/{document_id}
Authorization: Bearer <token>
```

**Response:**
```json
{
  "id": 123,
  "name": "example.pdf",
  "doc_id": "uuid-here",
  "document_type": ".pdf",
  "processing_status": "completed",
  "chunk_count": 45,
  "file_size_bytes": 1024000,
  "error_message": null,
  "s3_url": "s3://bucket/path/to/file.pdf",
  "tenant_id": 1,
  "created_at": "2025-10-06T18:00:00Z",
  "updated_at": "2025-10-06T18:05:00Z"
}
```

---

### 4. Delete Document

```http
DELETE /api/documents/{document_id}
Authorization: Bearer <token>
```

**Response:** `204 No Content`

**What Gets Deleted:**
1. File from S3 storage
2. Embeddings from vector database
3. Database records (document + processing jobs)

---

### 5. Semantic Search

```http
POST /api/documents/search
Authorization: Bearer <token>
Content-Type: application/json

{
  "query": "What is the capital of France?",
  "limit": 5,
  "document_types": [".pdf", ".md"],
  "document_ids": ["doc-uuid-1", "doc-uuid-2"]
}
```

**Request Body:**
- `query` (string, required) - Search query
- `limit` (int, default: 5, max: 50) - Number of results
- `document_types` (array, optional) - Filter by document types
- `document_ids` (array, optional) - Filter by specific documents

**Response:**
```json
{
  "query": "What is the capital of France?",
  "total_results": 3,
  "results": [
    {
      "chunk_id": "doc-uuid_chunk_12",
      "text": "Paris is the capital and largest city of France...",
      "document_id": "doc-uuid",
      "document_name": "geography.pdf",
      "chunk_index": 12,
      "distance": 0.234,
      "metadata": {
        "document_type": ".pdf",
        "tenant_id": 1,
        "page_count": 150,
        "created_at": "2025-10-06T18:00:00Z"
      }
    }
  ]
}
```

---

### 6. Get Processing Jobs

```http
GET /api/documents/{document_id}/processing-jobs
Authorization: Bearer <token>
```

**Response:**
```json
[
  {
    "id": 456,
    "document_id": 123,
    "status": "completed",
    "processor_type": ".pdf",
    "error_message": null,
    "chunks_created": 45,
    "processing_time_seconds": 12.5,
    "created_at": "2025-10-06T18:00:00Z",
    "updated_at": "2025-10-06T18:00:12Z"
  }
]
```

---

### 7. Reprocess Document

```http
POST /api/documents/{document_id}/reprocess
Authorization: Bearer <token>
```

**Response:**
```json
{
  "message": "Document queued for reprocessing",
  "document_id": 123,
  "job_id": 457
}
```

**When to Use:**
- Previous processing failed
- Tenant configuration changed (chunk size, embedding model)
- Document needs re-indexing

---

## Configuration

### Environment Variables

Add these to your `.env` file:

```bash
# Vector Database
VECTOR_STORE_TYPE=chroma
CHROMA_PERSIST_DIR=./chroma_db
CHROMA_COLLECTION_PREFIX=tenant

# S3 Storage
S3_BUCKET_NAME=your-bucket-name
S3_REGION=us-east-1
AWS_ACCESS_KEY_ID=your-access-key
AWS_SECRET_ACCESS_KEY=your-secret-key

# Document Processing
MAX_FILE_SIZE_MB=50
ALLOWED_DOCUMENT_TYPES=[".pdf", ".md", ".txt", ".docx"]
TEMP_UPLOAD_DIR=./temp_uploads

# OpenAI (for embeddings)
OPENAI_API_KEY=your-openai-api-key
```

### Tenant Configuration

Each tenant needs configuration in the `tenant_configs` table:

```sql
INSERT INTO tenant_configs (tenant_id, chunk_size, chunk_overlap, vector_store_type, embedding_model_id)
VALUES (1, 1000, 200, 'chroma', 1);
```

**Parameters:**
- `chunk_size` - Number of characters per chunk (default: 1000)
- `chunk_overlap` - Overlap between chunks (default: 200)
- `vector_store_type` - Vector store to use (default: `chroma`)
- `embedding_model_id` - Reference to `llm_models` table

---

## Processing Flow

```
1. Upload Document
   ├─> Save to S3
   ├─> Create DB record (status: pending)
   └─> Create processing job (status: pending)

2. Background Processing (TODO: Implement with Celery/RQ)
   ├─> Download from S3
   ├─> Extract text (PDF/MD processor)
   ├─> Extract metadata
   ├─> Chunk text
   ├─> Generate embeddings
   ├─> Store in vector DB
   └─> Update status (completed/failed)

3. Search
   ├─> Generate query embedding
   ├─> Search vector DB
   └─> Return ranked results
```

---

## Error Handling

### Common Error Codes

- **400 Bad Request**
  - Invalid file type
  - File size exceeds limit
  - Missing tenant configuration
  - Invalid search query

- **403 Forbidden**
  - User doesn't have access to document
  - Document belongs to different tenant

- **404 Not Found**
  - Document doesn't exist

- **413 Payload Too Large**
  - File exceeds MAX_FILE_SIZE_MB

- **500 Internal Server Error**
  - S3 upload/download failed
  - Embedding generation failed
  - Vector store operation failed

---

## Database Schema

### Documents Table
```sql
CREATE TABLE documents (
    id BIGSERIAL PRIMARY KEY,
    name VARCHAR NOT NULL,
    doc_id VARCHAR UNIQUE NOT NULL,
    s3_url VARCHAR UNIQUE,
    tenant_id BIGINT REFERENCES tenants(id) ON DELETE CASCADE,
    document_type VARCHAR NOT NULL,
    processing_status processingstatus DEFAULT 'pending',
    chunk_count INTEGER DEFAULT 0,
    file_size_bytes INTEGER,
    error_message VARCHAR,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);
```

### Document Processing Jobs Table
```sql
CREATE TABLE document_processing_jobs (
    id BIGSERIAL PRIMARY KEY,
    document_id BIGINT REFERENCES documents(id) ON DELETE CASCADE,
    status jobstatus DEFAULT 'pending',
    processor_type VARCHAR,
    error_message TEXT,
    chunks_created BIGINT DEFAULT 0,
    processing_time_seconds FLOAT,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);
```

---

## Dependencies

### Python Packages

Install required packages:

```bash
poetry add aioboto3 chromadb PyPDF2 openai
```

### ChromaDB Setup

ChromaDB will automatically create collections per tenant:
- Collection name format: `{CHROMA_COLLECTION_PREFIX}_{tenant_id}_documents`
- Storage: Persistent to `CHROMA_PERSIST_DIR`

---

## Testing

### 1. Upload a Document

```bash
curl -X POST "http://localhost:8000/api/documents/upload" \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -F "file=@example.pdf"
```

### 2. List Documents

```bash
curl -X GET "http://localhost:8000/api/documents" \
  -H "Authorization: Bearer YOUR_TOKEN"
```

### 3. Search Documents

```bash
curl -X POST "http://localhost:8000/api/documents/search" \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "query": "machine learning algorithms",
    "limit": 10
  }'
```

---

## Next Steps (TODO)

1. **Background Workers**
   - Implement Celery or RQ for async processing
   - Set up Redis for task queue
   - Create worker processes

2. **Additional Features**
   - Batch document upload
   - Document version control
   - Advanced filtering and sorting
   - Full-text search (in addition to semantic)
   - Document preview/download endpoints
   - Usage analytics and monitoring

3. **Optimizations**
   - Implement caching for frequently accessed embeddings
   - Add rate limiting
   - Optimize chunking strategies
   - Support for more document types (DOCX, PPT, etc.)

---

## Support

For issues or questions:
1. Check logs in `logs/` directory
2. Review document processing jobs for error messages
3. Verify tenant configuration is set correctly
4. Ensure S3 and OpenAI credentials are valid

