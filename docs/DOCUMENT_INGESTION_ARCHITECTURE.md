# Document Ingestion Architecture

## Overview

This document describes the document ingestion and RAG (Retrieval-Augmented Generation) architecture implemented in Just-EdTech-BE.

## Architecture Components

### 1. Database Models

#### Documents (`app/models/documents.py`)
- Stores document metadata
- Fields: `name`, `doc_id`, `s3_url`, `tenant_id`, `document_type`, `processing_status`, `chunk_count`
- Relationships: belongs to `Tenant`, has many `DocumentProcessingJobs`

#### DocumentProcessingJob (`app/models/processing_jobs.py`)
- Tracks async document processing jobs
- Fields: `document_id`, `status`, `processor_type`, `error_message`, `chunks_created`
- Status: `pending`, `processing`, `completed`, `failed`

#### TenantConfig (Enhanced)
- Added: `chunk_size`, `chunk_overlap`, `vector_store_type`
- Allows per-tenant customization of document processing

### 2. Vector Store Abstraction (`app/services/vector_store/`)

```
VectorStore (ABC)
├── ChromaDBStore (Current Implementation)
├── PineconeStore (Future)
└── WeaviateStore (Future)
```

**Why?** Easily switch between vector databases without changing business logic.

**Methods:**
- `add_chunks()` - Store document chunks with embeddings
- `search()` - Semantic search using vector similarity
- `delete_document()` - Remove all chunks for a document
- `get_document_chunks()` - Retrieve chunks
- `update_metadata()` - Update chunk metadata
- `get_collection_stats()` - Get statistics

### 3. Document Processing (`app/services/document_processing/`)

```
DocumentProcessor (ABC)
├── PDFProcessor
├── MarkdownProcessor
└── Easy to add more (DocxProcessor, etc.)
```

**Factory Pattern:** `ProcessorFactory.get_processor(file_path)` returns appropriate processor.

**Chunker:** Splits text into manageable pieces
- Strategies: `fixed`, `sentence`, `paragraph`
- Configurable: `chunk_size`, `chunk_overlap`

### 4. Embedding Service (`app/services/embeddings/`)

- Generates vector embeddings for text
- Currently: OpenAI embeddings
- Extensible to other providers (HuggingFace, Cohere, etc.)

### 5. Document Service (`app/services/document_service.py`)

**Main Orchestrator** that ties everything together:

```python
async def process_document(document_id, db):
    1. Get document from DB
    2. Extract text (using appropriate processor)
    3. Chunk text (using Chunker)
    4. Generate embeddings (using EmbeddingService)
    5. Store in vector DB (using VectorStore)
    6. Update database records
```

```python
async def search_documents(query, tenant_id, limit):
    1. Generate query embedding
    2. Search vector store
    3. Return ranked results
```

## Data Flow

### Upload & Processing Flow

```
User Upload
    ↓
API Endpoint
    ↓
Save to S3 + Create DB Record
    ↓
Queue Background Job
    ↓
Worker Process:
    ├─ Download File
    ├─ Extract Text (Processor)
    ├─ Chunk Text (Chunker)
    ├─ Generate Embeddings
    ├─ Store in ChromaDB
    └─ Update Status
```

### Query Flow

```
User Query
    ↓
Generate Query Embedding
    ↓
Search ChromaDB (Similarity)
    ↓
Return Top K Chunks
    ↓
Optional: Generate LLM Answer
```

## Data Storage

### PostgreSQL Stores:
- Document metadata (name, status, tenant)
- Processing job status
- User/tenant information
- Configuration

### ChromaDB Stores:
- Document chunks (text)
- Embeddings (vectors)
- Chunk metadata (page, index, etc.)

### S3 Stores:
- Original document files

## Configuration

Add to `.env`:

```env
# Vector Store
VECTOR_STORE_TYPE=chroma
CHROMA_PERSIST_DIR=./chroma_db

# OpenAI
OPENAI_API_KEY=your_key_here
OPENAI_EMBEDDING_MODEL=text-embedding-3-small

# Document Processing
MAX_FILE_SIZE_MB=50
CHUNK_SIZE=1000
CHUNK_OVERLAP=200
```

## Adding New Document Types

1. Create processor class:
```python
class DocxProcessor(DocumentProcessor):
    supported_extensions = [".docx"]
    
    def extract_text(self, file_path):
        # Implementation
        pass
```

2. Register in factory:
```python
ProcessorFactory.register_processor(".docx", DocxProcessor)
```

## Adding New Vector Stores

1. Implement `VectorStore` interface:
```python
class PineconeStore(VectorStore):
    async def add_chunks(...):
        # Pinecone implementation
```

2. Register in factory:
```python
# In factory.py
elif store_type == "pinecone":
    return PineconeStore(...)
```

3. Update config:
```env
VECTOR_STORE_TYPE=pinecone
PINECONE_API_KEY=your_key
```

## Database Migrations

Run migrations to apply schema changes:

```bash
alembic revision --autogenerate -m "add_document_processing_models"
alembic upgrade head
```

## Dependencies

Required packages (add to `pyproject.toml`):

```toml
[tool.poetry.dependencies]
chromadb = "^0.4.0"
pypdf2 = "^3.0.0"
openai = "^1.0.0"
```

## API Endpoints (To Be Implemented)

```
POST /api/v1/documents/upload
GET  /api/v1/documents
GET  /api/v1/documents/{id}
DELETE /api/v1/documents/{id}
POST /api/v1/documents/{id}/process
GET  /api/v1/documents/{id}/chunks
POST /api/v1/rag/query
GET  /api/v1/jobs/{id}
```

## Testing

```python
# Test document processing
from app.services.document_service import DocumentService

service = DocumentService()
await service.process_document(document_id=1, db=session)

# Test search
results = await service.search_documents(
    query="What is the diagnosis?",
    tenant_id=1,
    limit=5
)
```

## Security Considerations

1. **Tenant Isolation**: All queries filtered by `tenant_id`
2. **File Validation**: Check file types and sizes
3. **S3 Security**: Use IAM roles, signed URLs
4. **Vector Store**: Separate collections per tenant

## Performance Tips

1. **Batch Processing**: Process documents in background
2. **Chunking**: Optimize chunk size for your use case
3. **Embeddings**: Cache if reusing same texts
4. **Vector Search**: Use appropriate similarity metrics
5. **Database**: Index on `tenant_id`, `processing_status`

## Monitoring

Track these metrics:
- Documents processed per hour
- Processing failures
- Average chunk count per document
- Query latency
- Vector store size per tenant

## Future Enhancements

- [ ] Support for more document types (DOCX, Excel, Images)
- [ ] Advanced chunking strategies (semantic chunking)
- [ ] Multiple embedding models per tenant
- [ ] Hybrid search (keyword + semantic)
- [ ] Document versioning
- [ ] Batch upload API
- [ ] Webhook notifications
- [ ] Re-embedding with different models

## Troubleshooting

### Common Issues

**Problem:** Documents stuck in "processing" status
**Solution:** Check worker logs, ensure ChromaDB is running

**Problem:** Empty search results
**Solution:** Verify embeddings are generated, check tenant_id filter

**Problem:** Out of memory during processing
**Solution:** Reduce chunk size, process in batches

**Problem:** Slow search queries
**Solution:** Optimize chunk_size, use appropriate vector index

## Support

For questions or issues, refer to:
- ChromaDB docs: https://docs.trychroma.com/
- OpenAI embeddings: https://platform.openai.com/docs/guides/embeddings

