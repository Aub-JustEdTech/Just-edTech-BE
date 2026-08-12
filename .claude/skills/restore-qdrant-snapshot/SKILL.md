---
name: restore-qdrant-snapshot
description: Restores a Qdrant snapshot dump (e.g. shared by a teammate) into the local Qdrant container for whatever tenant it belongs to, and makes that tenant's data actually visible in the app. Use when the user has `.snapshot` files to load, wants to import/restore a vector DB dump, sync a colleague's Qdrant data locally, or is seeing empty results (heatmap, RAG, etc.) after a restore.
allowed-tools: Bash, Read
---

When restoring one or more Qdrant snapshot files into the local vector DB, treat the tenant ID as **derived from the snapshot itself, never hardcoded** — it will be `2` for one colleague's dump and `7` for another's. Everything below uses `{tenant_id}` as a placeholder filled in per-snapshot.

## 1. Locate the Snapshot Files and the Running Qdrant Port

```bash
ls -la dump/            # or wherever the user says the snapshots are
grep -A 5 "qdrant:" docker-compose.yml   # confirm the host port mapping (local dev: 6343 → container 6333)
```

## 2. Derive the Target Collection Name and Tenant ID from the Filename

Qdrant snapshot files are always named:

```
{collection_name}-{node_id}-{timestamp}.snapshot
```

Strip the trailing `-{node_id}-{timestamp}.snapshot` to get the collection name. In this project, collections follow `{QDRANT_COLLECTION_PREFIX}_{tenant_id}_{type}` (check `QDRANT_COLLECTION_PREFIX` in `.env` — default `justedtech`; `type` is typically `documents`, `summaries`, or `images`). So a file like:

```
justedtech_2_documents-102623197053986-2026-08-04-09-14-57.snapshot
```

restores into collection `justedtech_2_documents` — extract `tenant_id=2` from the middle segment. **Do this extraction per file** — don't assume every snapshot in a batch belongs to the same tenant; check each filename.

## 3. Check for Existing Collections Before Restoring

**Never overwrite silently** — restoring into a collection name that already has data replaces it.

```bash
curl -s http://localhost:6343/collections | python3 -m json.tool
```

If the target collection name already exists and has points, STOP and confirm with the user before proceeding (this is destructive and not easily reversible).

## 4. Upload Each Snapshot

Qdrant's snapshot-upload endpoint creates the collection (if missing) and restores its data in one call — no need to copy files into the container:

```bash
curl -X POST 'http://localhost:6343/collections/{collection_name}/snapshots/upload?priority=snapshot' \
  -H 'Content-Type: multipart/form-data' \
  -F 'snapshot=@dump/{filename}.snapshot'
```

Repeat once per snapshot file. A successful response looks like `{"result":true,"status":"ok",...}`.

## 5. Verify the Restore Landed

```bash
curl -s http://localhost:6343/collections/{collection_name} \
  | python3 -c "import sys,json; d=json.load(sys.stdin)['result']; print(d['points_count'], 'points, status:', d['status'])"
```

Expect `status: green` and a non-zero `points_count` matching what the teammate reported (ask them if unsure).

## 6. Verify Retrieval Actually Works (not just point count)

Run the project's real embedding + search path against the restored `{tenant_id}` inside the API container — this confirms embeddings + vector search both work, not just that bytes landed:

```bash
docker exec <api_container_name> python3 -c "
import asyncio
from app.services.embeddings.embedding_service import EmbeddingService
from app.services.vector_store.factory import VectorStoreFactory

async def main():
    embed_service = EmbeddingService()
    store = VectorStoreFactory.create()
    vector = await embed_service.generate_single_embedding('<query relevant to the restored content>')
    results = await store.search(query_embedding=vector, tenant_id={tenant_id}, limit=5)
    print(f'Got {len(results)} results')
    for r in results:
        print(r['score'], r['text'][:120])

asyncio.run(main())
"
```

Pick a query relevant to the actual restored content (peek at a payload first via `/points/scroll` if unsure what's in there — don't guess blindly).

## 7. Make the Data Actually Visible in the App

A restored Qdrant collection is necessary but not sufficient — nothing in the UI shows it until the Postgres side lines up. Check, don't assume:

**a. Does `tenant_id` exist as a real tenant in Postgres?**

```bash
psql ... -c "select id, name from tenants where id = {tenant_id};"
```

If missing, you need a `tenants` row before anything with a `tenant_id` FK (users, schools, chatbot_configs, documents) can reference it. **Do not invent a tenant name from the document content** — ask the user/colleague what this tenant is actually called. A guessed name is a cosmetic error waiting to be found later; say explicitly that you're guessing if you must proceed without an answer, e.g.:

```sql
INSERT INTO tenants (id, name, domain, created_at, updated_at)
VALUES ({tenant_id}, '<confirmed or explicitly-flagged-as-guessed name>', '<domain>', now(), now());
SELECT setval('tenants_id_seq', (SELECT MAX(id) FROM tenants));
```

**b. Can the logged-in user actually see it?**

Tenant-scoped endpoints in this app (e.g. `/heatmap/engine/districts`, `/rag/query`) key off `current_user.tenant_id` directly — there is **no cross-tenant "view as" override**, even for `super_admin`. The only way to view tenant `{tenant_id}`'s data through an existing login is for that user's own `tenant_id` column to equal `{tenant_id}`. Check who the user is testing as and confirm this before assuming the restore is broken:

```bash
psql ... -c "select id, email, tenant_id from users where email = '<test user email>';"
```

If it doesn't match, either repoint that user (`UPDATE users SET tenant_id = {tenant_id} WHERE id = ...` — confirm with the user first if it's a shared/real account, not a throwaway dev login) or create a new user scoped to `{tenant_id}`.

**c. Feature-specific Postgres data the vector store alone doesn't provide**

Some features join Qdrant data against Postgres tables that a Qdrant-only snapshot never populates. Known example in this repo: the **Heatmap Engine** (`app/services/heatmap_engine/service.py`) loops over `School` rows for the tenant *before* ever touching Qdrant — zero `schools` rows means "No activity found" even with thousands of matching vectors. Fix:

```bash
docker exec <api_container_name> python scripts/school_data/seed_schools.py --tenant-id {tenant_id}
```

This is idempotent (safe to re-run) and scoped to the one tenant. If some other feature is empty after a restore, check its service layer for a similar Postgres-side dependency before concluding retrieval is broken — grep the service for filters against SQLAlchemy models, not just Qdrant calls.

**d. Timeframe/date filters can hide real data**

If a feature still shows nothing after (a)–(c), check whether a date/timeframe filter's "current" period actually overlaps the restored data's dates — don't assume the filter is broken. e.g. this repo's heatmap "Last Year" preset uses the machine's current date to pick an academic-year bucket; if the restored data predates that bucket, widen the range (e.g. "3 Years") before troubleshooting further.

## Constraints

- This targets **local dev Qdrant only** (`docker-compose` service, port `6343` by default) — never point this at a shared/staging/production Qdrant URL without explicit confirmation.
- Always check step 3 before uploading — do not restore over an existing non-empty collection without the user's go-ahead.
- Never guess a real tenant's name/identity and treat the guess as fact — flag it as a guess and ask the source colleague to confirm.
- Repointing or creating `users`/`tenants` rows touches auth data — confirm with the user before mutating a real account, even in local dev.
- No app code or config changes are needed for the vector store itself: `QDRANT_URL` in `docker-compose.yml` already points the API/worker containers at the same Qdrant instance.
- If `docker-compose ps qdrant` shows the container isn't up, start it first: `docker-compose up -d qdrant`.
