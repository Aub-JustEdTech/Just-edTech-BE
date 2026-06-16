"""
Test script to create a Qdrant collection and verify it exists.
This script works without Docker - it connects to a local Qdrant server.
"""

import asyncio
import logging
import sys
from pathlib import Path

# Add parent directory to path to import app modules
sys.path.insert(0, str(Path(__file__).parent.parent))

from qdrant_client import QdrantClient
from qdrant_client.http.exceptions import UnexpectedResponse

from app.core.config import settings
from app.services.vector_store.factory import VectorStoreFactory

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


def check_qdrant_connection():
    """Check if Qdrant server is accessible"""
    try:
        logger.info("Checking Qdrant server connection...")
        # Disable version check for compatibility
        client = QdrantClient(url=settings.QDRANT_URL, check_compatibility=False)
        # Try to get collections to verify connection
        collections = client.get_collections()
        logger.info(f"✓ Successfully connected to Qdrant at {settings.QDRANT_URL}")
        logger.info(f"  Found {len(collections.collections)} existing collections")
        return True
    except Exception as e:
        logger.error(f"✗ Cannot connect to Qdrant server at {settings.QDRANT_URL}")
        logger.error(f"  Error: {e}")
        logger.error("\nTo start Qdrant locally, you can:")
        logger.error("  1. Install Qdrant: https://qdrant.tech/documentation/guides/installation/")
        logger.error("  2. Run: qdrant")
        logger.error("  3. Or use Docker: docker run -p 6333:6333 qdrant/qdrant")
        return False


async def test_qdrant_collection():
    """Test creating a Qdrant collection and verifying it exists"""
    try:
        logger.info("=" * 60)
        logger.info("Testing Qdrant Collection Creation")
        logger.info("=" * 60)

        # Check connection first
        logger.info(f"\nQdrant URL: {settings.QDRANT_URL}")
        if not check_qdrant_connection():
            return False

        # Create Qdrant store instance
        logger.info("\n1. Creating Qdrant store instance...")
        vector_store = VectorStoreFactory.create("qdrant")
        logger.info("✓ Qdrant store created successfully")

        # Test tenant ID
        test_tenant_id = 1
        collection_name = f"{settings.QDRANT_COLLECTION_PREFIX}_{test_tenant_id}_documents"
        logger.info(f"\n2. Target collection name: {collection_name}")

        # Create collection by adding test chunks
        logger.info("\n3. Creating collection by adding test chunks...")
        test_document_id = "test_doc_001"
        test_chunks = ["This is a test chunk for Qdrant collection."]
        test_embeddings = [[0.1] * 1536]  # Dummy embedding vector (1536 dimensions for OpenAI)
        test_metadatas = [
            {
                "document_id": test_document_id,
                "tenant_id": test_tenant_id,
                "document_name": "Test Document",
                "chunk_index": 0,
            }
        ]

        result = await vector_store.add_chunks(
            document_id=test_document_id,
            chunks=test_chunks,
            embeddings=test_embeddings,
            metadatas=test_metadatas,
        )

        if result:
            logger.info("✓ Test chunks added successfully - collection created")
        else:
            logger.error("✗ Failed to add test chunks")
            return False

        # Verify collection exists by getting stats
        logger.info("\n4. Verifying collection exists...")
        stats = await vector_store.get_collection_stats(test_tenant_id)

        if stats:
            logger.info(f"✓ Collection exists!")
            logger.info(f"  - Collection name: {stats.get('collection_name', 'N/A')}")
            logger.info(f"  - Total chunks: {stats.get('total_chunks', 0)}")
            logger.info(f"  - Total documents: {stats.get('total_documents', 0)}")
        else:
            logger.error("✗ Failed to get collection stats")
            return False

        # Also verify by checking if we can search
        logger.info("\n5. Verifying collection is searchable...")
        test_query_embedding = [0.1] * 1536
        search_results = await vector_store.search(
            query_embedding=test_query_embedding,
            tenant_id=test_tenant_id,
            limit=1,
        )

        if search_results is not None:
            logger.info(f"✓ Collection is searchable - found {len(search_results)} results")
        else:
            logger.error("✗ Collection search failed")
            return False

        # Clean up test data
        logger.info("\n6. Cleaning up test data...")
        delete_result = await vector_store.delete_document(test_document_id, test_tenant_id)
        if delete_result:
            logger.info("✓ Test document deleted successfully")
        else:
            logger.warning("⚠ Failed to delete test document (may not exist)")

        logger.info("\n" + "=" * 60)
        logger.info("✓ All tests passed! Qdrant collection works correctly.")
        logger.info("=" * 60)
        return True

    except Exception as e:
        logger.error(f"\n✗ Error during testing: {e}", exc_info=True)
        return False


if __name__ == "__main__":
    success = asyncio.run(test_qdrant_collection())
    sys.exit(0 if success else 1)
