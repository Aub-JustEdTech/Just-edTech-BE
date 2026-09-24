"""Diagnostic: audit the `{prefix}_{tenant_id}_documents` (chunks) Qdrant
collection against Postgres `documents.chunk_count`.

Detects the "silent Qdrant write failure" case: `add_chunks_returning_ids`
in app/services/vector_store/qdrant_store.py catches all exceptions and
returns `[]` instead of raising, and app/tasks/document_pipeline.py stage 5
does not verify the returned point-id count before marking the document
COMPLETED with `chunk_count = len(ctx.chunks)`. Under load (Qdrant write
pressure, timeouts) this produces documents whose Postgres row claims N
chunks while Qdrant actually has 0 (or fewer) points for that doc_id.

Read-only against Qdrant + Postgres. Safe to run in production.
NOTE: this scrolls the entire documents collection for the tenant, which can
be slow/heavy for large collections (tens of thousands of points) - it only
fetches the `document_id` payload field to keep it cheap.

Usage (inside the api container):
    docker exec just-edtech-api python scripts/diagnostics/tenant_qdrant_chunk_audit.py --tenant-id 4
"""

import argparse
import asyncio
import sys
from collections import Counter

from qdrant_client import QdrantClient
from qdrant_client.http import models
from sqlalchemy import select

from app.core.config import settings
from app.db.connector import AsyncSessionLocal
from app.models.documents import Document, ProcessingStatus

SCROLL_BATCH = 1024


async def count_chunks_per_doc(client: QdrantClient, collection_name: str) -> Counter:
    counts: Counter = Counter()
    offset = None
    while True:
        batch, offset = await asyncio.to_thread(
            client.scroll,
            collection_name=collection_name,
            limit=SCROLL_BATCH,
            offset=offset,
            with_payload=models.PayloadSelectorInclude(include=["document_id"]),
            with_vectors=False,
        )
        for p in batch:
            doc_id = (p.payload or {}).get("document_id")
            if doc_id is not None:
                counts[doc_id] += 1
        if offset is None:
            break
    return counts


async def main(tenant_id: int) -> None:
    client = QdrantClient(url=settings.QDRANT_URL, check_compatibility=False)
    collection_name = f"{settings.QDRANT_COLLECTION_PREFIX}_{tenant_id}_documents"

    try:
        info = await asyncio.to_thread(client.get_collection, collection_name)
        print(f"Collection: {collection_name}")
        print(f"Reported point count: {info.points_count}\n")
    except Exception as exc:
        print(f"Could not get collection '{collection_name}': {exc}", file=sys.stderr)
        sys.exit(1)

    print("Scrolling collection to count chunk points per document_id (this may take a while)...")
    qdrant_counts = await count_chunks_per_doc(client, collection_name)
    print(f"Distinct doc_ids with at least one chunk point in Qdrant: {len(qdrant_counts)}")
    print(f"Total chunk points scrolled: {sum(qdrant_counts.values())}\n")

    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(
                Document.doc_id,
                Document.chunk_count,
                Document.processing_status,
                Document.name,
            ).where(Document.tenant_id == tenant_id)
        )
        db_rows = result.all()

    mismatches = []
    completed_missing_in_qdrant = 0
    completed_excess_in_qdrant = 0  # qdrant has MORE points than DB says -> likely duplicate from reprocess
    completed_deficit_in_qdrant = 0  # qdrant has FEWER (but >0) points than DB says -> partial silent failure
    for row in db_rows:
        db_chunk_count = row.chunk_count or 0
        qdrant_count = qdrant_counts.get(row.doc_id, 0)
        if row.processing_status == ProcessingStatus.COMPLETED and db_chunk_count > 0:
            if qdrant_count == 0:
                completed_missing_in_qdrant += 1
                mismatches.append((row.doc_id, row.name, db_chunk_count, qdrant_count, "MISSING"))
            elif qdrant_count > db_chunk_count:
                completed_excess_in_qdrant += 1
                ratio = qdrant_count / db_chunk_count
                kind = f"EXCESS x{ratio:.1f}"
                mismatches.append((row.doc_id, row.name, db_chunk_count, qdrant_count, kind))
            elif qdrant_count < db_chunk_count:
                completed_deficit_in_qdrant += 1
                mismatches.append((row.doc_id, row.name, db_chunk_count, qdrant_count, "DEFICIT"))

    total_completed = sum(
        1 for r in db_rows if r.processing_status == ProcessingStatus.COMPLETED and (r.chunk_count or 0) > 0
    )
    print("=== Postgres vs Qdrant chunk-count consistency (COMPLETED docs only) ===")
    print(f"COMPLETED docs with chunk_count > 0 in Postgres: {total_completed}")
    print(f"  -> 0 matching points in Qdrant (fully silent write failure):        {completed_missing_in_qdrant}")
    print(f"  -> qdrant has MORE points than DB (stale chunks from a reprocess    {completed_excess_in_qdrant}")
    print(f"     that never deleted the old set before re-adding):")
    print(f"  -> qdrant has FEWER (but >0) points than DB (partial write):        {completed_deficit_in_qdrant}")

    if mismatches:
        print("\nSample mismatches (doc_id, name, db_chunk_count, qdrant_count, kind):")
        for m in mismatches[:20]:
            print(f"  {m}")
        if len(mismatches) > 20:
            print(f"  ... and {len(mismatches) - 20} more")

    print(
        "\nInterpretation:\n"
        "  MISSING/DEFICIT -> silent Qdrant write failures: Postgres believes\n"
        "    chunking succeeded but some/all vectors never landed. Reduces the\n"
        "    'documents' collection count without touching 'summaries', which was\n"
        "    already written earlier in the same pipeline run.\n"
        "  EXCESS (often exactly 2x, 3x...) -> the document was reprocessed one or\n"
        "    more times (retry / scripts/school_data/reprocess_failed_documents.py /\n"
        "    POST /documents/{id}/reprocess) and the OLD chunk points were never\n"
        "    deleted before the new ones were added (there is no call to\n"
        "    vector_store.delete_document() before re-running stage 5). Postgres\n"
        "    chunk_count only reflects the last run, but Qdrant accumulates every\n"
        "    run's points. This inflates the 'documents' collection too, just less\n"
        "    visibly than the summaries collection because chunk_count in Postgres\n"
        "    hides it."
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tenant-id", type=int, required=True)
    args = parser.parse_args()
    try:
        asyncio.run(main(args.tenant_id))
    except KeyboardInterrupt:
        sys.exit(130)
