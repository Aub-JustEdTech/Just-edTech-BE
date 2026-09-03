"""Diagnostic: audit the `{prefix}_{tenant_id}_summaries` Qdrant collection.

Checks for the most likely cause of "too many summaries": summaries are
written with a fresh random point-id and are never deleted/deduped
(`add_document_summary` in app/services/vector_store/qdrant_store.py always
inserts; `delete_document_summary` exists but is never called by any delete
or reprocess flow). Any document that goes through the pipeline more than
once (e.g. via scripts/school_data/reprocess_failed_documents.py, or the
`/documents/{id}/reprocess` endpoint) accumulates one extra summary point
per attempt, even though it only ever gets ONE final set of chunks.

Read-only against Qdrant + Postgres. Safe to run in production.

Usage (inside the api container):
    docker exec just-edtech-api python scripts/diagnostics/tenant_qdrant_summary_audit.py --tenant-id 4
"""

import argparse
import asyncio
import sys
from collections import Counter, defaultdict

from qdrant_client import QdrantClient
from sqlalchemy import select

from app.core.config import settings
from app.db.connector import AsyncSessionLocal
from app.models.documents import Document

SCROLL_BATCH = 512


async def scroll_all(client: QdrantClient, collection_name: str) -> list[dict]:
    points: list[dict] = []
    offset = None
    while True:
        batch, offset = await asyncio.to_thread(
            client.scroll,
            collection_name=collection_name,
            limit=SCROLL_BATCH,
            offset=offset,
            with_payload=True,
            with_vectors=False,
        )
        for p in batch:
            points.append(p.payload or {})
        if offset is None:
            break
    return points


async def main(tenant_id: int) -> None:
    client = QdrantClient(url=settings.QDRANT_URL, check_compatibility=False)
    collection_name = f"{settings.QDRANT_COLLECTION_PREFIX}_{tenant_id}_summaries"

    try:
        info = await asyncio.to_thread(client.get_collection, collection_name)
        print(f"Collection: {collection_name}")
        print(f"Reported point count: {info.points_count}\n")
    except Exception as exc:
        print(f"Could not get collection '{collection_name}': {exc}", file=sys.stderr)
        sys.exit(1)

    payloads = await scroll_all(client, collection_name)
    total_points = len(payloads)

    by_doc_id: dict[int, list[dict]] = defaultdict(list)
    missing_doc_id = 0
    for p in payloads:
        doc_id = p.get("document_id")
        if doc_id is None:
            missing_doc_id += 1
            continue
        by_doc_id[int(doc_id)].append(p)

    distinct_docs = len(by_doc_id)
    duplicate_docs = {doc_id: pts for doc_id, pts in by_doc_id.items() if len(pts) > 1}
    duplicate_points = sum(len(pts) for pts in duplicate_docs.values())

    print("=== Summary point counts ===")
    print(f"Total summary points scrolled:      {total_points}")
    print(f"Distinct document_ids represented:  {distinct_docs}")
    print(f"Points with missing document_id:    {missing_doc_id}")
    print(f"Documents with >1 summary point:    {len(duplicate_docs)}")
    print(f"  -> extra/duplicate points from those docs: {duplicate_points - len(duplicate_docs)}")

    if duplicate_docs:
        dup_counts = Counter(len(pts) for pts in duplicate_docs.values())
        print("\n  Duplicate distribution (points per doc -> number of docs):")
        for n_points, n_docs in sorted(dup_counts.items()):
            print(f"    {n_points} points/doc: {n_docs} docs")
        sample_ids = list(duplicate_docs.keys())[:10]
        print(f"\n  Sample duplicated document_ids: {sample_ids}")

    # Cross-check against Postgres: does the document still exist / have chunks?
    doc_ids = list(by_doc_id.keys())
    orphaned_deleted = 0
    orphaned_no_chunks = 0
    if doc_ids:
        async with AsyncSessionLocal() as db:
            CHUNK = 1000
            db_docs: dict[int, Document] = {}
            for i in range(0, len(doc_ids), CHUNK):
                batch_ids = doc_ids[i : i + CHUNK]
                result = await db.execute(
                    select(Document).where(Document.id.in_(batch_ids))
                )
                for d in result.scalars().all():
                    db_docs[d.id] = d

        for doc_id in doc_ids:
            d = db_docs.get(doc_id)
            if d is None:
                orphaned_deleted += 1
            elif (d.chunk_count or 0) == 0:
                orphaned_no_chunks += 1

    print("\n=== Cross-check against Postgres `documents` table ===")
    print(f"Summary doc_ids with NO matching Document row (deleted, never cleaned up): {orphaned_deleted}")
    print(f"Summary doc_ids whose Document row has chunk_count == 0:                  {orphaned_no_chunks}")

    print(
        "\nInterpretation: if duplicate summary points and/or orphaned summaries "
        "are a large share of the total, the mismatch is caused by re-running the "
        "document pipeline (retries / scripts/school_data/reprocess_failed_documents.py "
        "/ POST /documents/{id}/reprocess) without ever calling "
        "vector_store.delete_document_summary() first. Each reprocess of a doc adds "
        "one more summary point but only ever produces the final set of chunks once."
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tenant-id", type=int, required=True)
    args = parser.parse_args()
    try:
        asyncio.run(main(args.tenant_id))
    except KeyboardInterrupt:
        sys.exit(130)
