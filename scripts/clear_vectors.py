"""Utility script to clear vectors in the vector store.

Usage:
    poetry run python scripts/clear_vectors.py --tenant-id 1
    poetry run python scripts/clear_vectors.py --all-tenants
"""

import argparse
import asyncio
import os
import sys

import chromadb

from app.core.config import settings
from app.services.vector_store.chroma_store import ChromaDBStore


async def clear_tenant(tenant_id: int) -> bool:
    # Use absolute persist directory from settings to avoid CWD-dependent resolution
    store = ChromaDBStore(persist_directory=settings.CHROMA_PERSIST_DIR)
    return await store.clear_tenant(tenant_id)


def list_tenant_ids_from_collections() -> list[int]:
    # settings.CHROMA_PERSIST_DIR is absolute (normalized in config)
    client = chromadb.PersistentClient(path=settings.CHROMA_PERSIST_DIR)
    collections = client.list_collections()
    tenant_ids: list[int] = []
    prefix = f"{settings.CHROMA_COLLECTION_PREFIX}_"
    for c in collections:
        # Expected name format: tenant_{tenant_id}_documents
        name = c.name
        if name.startswith(prefix) and name.endswith("_documents"):
            middle = name[len(prefix):-len("_documents")]
            try:
                tenant_ids.append(int(middle))
            except ValueError:
                continue
    return sorted(set(tenant_ids))


async def main():
    parser = argparse.ArgumentParser(description="Clear vectors from ChromaDB")
    parser.add_argument("--tenant-id", type=int, help="Tenant ID to clear")
    parser.add_argument(
        "--all-tenants", action="store_true", help="Clear all tenant collections"
    )
    args = parser.parse_args()

    if not args.tenant_id and not args.all_tenants:
        print("Error: specify --tenant-id or --all-tenants", file=sys.stderr)
        sys.exit(1)

    if args.tenant_id:
        ok = await clear_tenant(args.tenant_id)
        sys.exit(0 if ok else 2)

    # All tenants
    tenant_ids = list_tenant_ids_from_collections()
    print(f"Found tenant collections: {tenant_ids}")
    overall_ok = True
    for tid in tenant_ids:
        ok = await clear_tenant(tid)
        overall_ok = overall_ok and ok
    sys.exit(0 if overall_ok else 2)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        sys.exit(130)


