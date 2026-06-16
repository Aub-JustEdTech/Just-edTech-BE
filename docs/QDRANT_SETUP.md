# Qdrant Vector Store Setup

This document explains how to set up and test Qdrant as a vector store for the Just-EdTech application.

## Prerequisites

Qdrant server must be running before you can use it. You have several options:

### Option 1: Download and Run Qdrant Binary (Recommended for Local Development)

```bash
# Download Qdrant binary (Linux x86_64)
wget https://github.com/qdrant/qdrant/releases/download/v1.7.0/qdrant-x86_64-unknown-linux-gnu.tar.gz

# Extract
tar -xzf qdrant-x86_64-unknown-linux-gnu.tar.gz

# Run Qdrant (in background)
./qdrant &

# Verify it's running
curl http://localhost:6333/health
```

### Option 2: Use Docker (if available)

```bash
docker run -d -p 6333:6333 -p 6334:6334 --name qdrant qdrant/qdrant

# Verify it's running
curl http://localhost:6333/health
```

### Option 3: Install via Package Manager

Check Qdrant documentation for your platform: https://qdrant.tech/documentation/guides/installation/

## Configuration

Update your `.env` file to use Qdrant:

```env
VECTOR_STORE_TYPE=qdrant
QDRANT_URL=http://localhost:6333
QDRANT_COLLECTION_PREFIX=tenant
```

## Testing Qdrant Collection

### Run the Test Script

```bash
# Make sure Qdrant is running first
poetry run python scripts/test_qdrant.py
```

The test script will:
1. Check if Qdrant server is accessible
2. Create a Qdrant store instance
3. Create a test collection by adding sample chunks
4. Verify the collection exists
5. Test searching the collection
6. Clean up test data

### Expected Output

```
============================================================
Testing Qdrant Collection Creation
============================================================

Qdrant URL: http://localhost:6333
Checking Qdrant server connection...
✓ Successfully connected to Qdrant at http://localhost:6333
  Found 0 existing collections

1. Creating Qdrant store instance...
✓ Qdrant store created successfully

2. Target collection name: tenant_1_documents

3. Creating collection by adding test chunks...
✓ Test chunks added successfully - collection created

4. Verifying collection exists...
✓ Collection exists!
  - Collection name: tenant_1_documents
  - Total chunks: 1
  - Total documents: 1

5. Verifying collection is searchable...
✓ Collection is searchable - found 1 results

6. Cleaning up test data...
✓ Test document deleted successfully

============================================================
✓ All tests passed! Qdrant collection works correctly.
============================================================
```

## Running the Server with Qdrant

Once Qdrant is running and configured:

```bash
# Start the FastAPI server
poetry run uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

The server will automatically use Qdrant when `VECTOR_STORE_TYPE=qdrant` is set in your `.env` file.

## Collection Naming

Collections are automatically created with the naming pattern:
```
{QDRANT_COLLECTION_PREFIX}_{tenant_id}_documents
```

For example: `tenant_1_documents`, `tenant_2_documents`, etc.

## Switching Between Vector Stores

You can easily switch between ChromaDB and Qdrant by changing the `VECTOR_STORE_TYPE` in your `.env`:

```env
# Use ChromaDB
VECTOR_STORE_TYPE=chroma

# Use Qdrant
VECTOR_STORE_TYPE=qdrant
```

No code changes are required - the factory pattern handles the switch automatically.

## Troubleshooting

### Connection Refused

If you see "Connection refused" errors:
- Make sure Qdrant server is running: `curl http://localhost:6333/health`
- Check the `QDRANT_URL` in your `.env` file
- Verify Qdrant is listening on port 6333

### Collection Not Found

Collections are created automatically when you first add chunks. If a collection doesn't exist, it will be created on the first `add_chunks` call.

### Port Already in Use

If port 6333 is already in use:
- Change the port in Qdrant configuration
- Update `QDRANT_URL` in `.env` to match the new port
