"""
OpenAI Batch API classifier for heatmap chunk-level taxonomy tagging.

Lifecycle:
  1. step6_accumulate_batch (in document_pipeline.py) inserts one row into
     `pending_classifications` per chunk after step5_store_vectors stores
     them in Qdrant. Rows start with status='pending'.

  2. submit_pending_batch() pulls all status='pending' rows, builds a JSONL
     file (one line per chunk using the prompt from Phase 1's eval
     harness), uploads to S3, submits via openai.batches.create with
     endpoint /v1/chat/completions, and flips rows to status='submitted'.

  3. poll_batch(batch_id) refreshes a job's status from OpenAI.

  4. apply_batch_results(batch_id) downloads the output JSONL, parses each
     result, calls qdrant set_payload per point (topics, action_types,
     subtopics, evidence_quote, off_topic, classified=true), upserts into
     heatmap_aggregate, and flips rows to status='applied'.

Cost: ~$155 for the one-off 2.5M-chunk backfill, ~$6-12/month incremental.
"""

import asyncio
import io
import json
import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.batch_classification_job import BatchClassificationJob
from app.models.heatmap_aggregate import HeatmapAggregate
from app.models.pending_classification import PendingClassification
from app.services.heatmap_ingest.prompt import (
    build_batch_request_line,
    serialize_batch_line,
)
from app.services.heatmap_ingest.taxonomy import ChunkClassification
from app.services.llm.client import (
    get_async_openai_client,
    get_llm_api_key,
    normalize_model_name,
    uses_openrouter,
)
from app.utils.s3 import S3Manager

logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class BatchClassifier:
    """
    Orchestrates OpenAI Batch API submissions for chunk classification.

    One instance per process; methods are async and take a session so they
    can be called from Celery tasks or scripts.
    """

    def __init__(self, model: str | None = None):
        self._model = normalize_model_name(
            model or getattr(
                settings, "HEATMAP_INGEST_CHUNK_CLASSIFIER_MODEL", "openai/gpt-4o-mini"
            )
        )
        get_llm_api_key()
        self._client = get_async_openai_client()
        # Reuse a single S3Manager instance.
        self._s3 = S3Manager(
            bucket_name=settings.S3_BUCKET_NAME,
            region_name=settings.S3_REGION,
            aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
            aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
        )

    # ------------------------------------------------------------------
    # 1. Submit
    # ------------------------------------------------------------------

    async def submit_pending_batch(self, db: AsyncSession) -> BatchClassificationJob | None:
        """
        Pull all status='pending' rows, build + upload JSONL, submit to
        OpenAI Batch API. Returns the new job row, or None if no pending
        rows existed.

        OpenRouter does not expose OpenAI's Batch API, so when
        LLM_API_PROVIDER=openrouter we classify chunks via concurrent
        chat-completions calls instead.
        """
        if uses_openrouter():
            return await self._submit_pending_via_direct_api(db)

        batch_size = int(getattr(settings, "HEATMAP_INGEST_BATCH_SIZE", 50_000))

        pending_rows = (
            (
                await db.execute(
                    select(PendingClassification)
                    .where(PendingClassification.status == "pending")
                    .order_by(PendingClassification.id)
                    .limit(batch_size)
                )
            )
            .scalars()
            .all()
        )
        if not pending_rows:
            logger.info("No pending classifications to submit.")
            return None

        logger.info(f"Building batch of {len(pending_rows)} chunks for submission.")

        # Build the JSONL payload.
        # Resolve state per document so the state vocabulary pack (A3b) is
        # injected into each chunk's prompt. Cached per call to avoid
        # repeated DB hits within a single batch.
        doc_state_cache: dict[int, str | None] = {}
        lines: list[str] = []
        for row in pending_rows:
            if row.document_id not in doc_state_cache:
                doc_state_cache[row.document_id] = await self._state_for_doc(
                    db, row.document_id
                )
            request = build_batch_request_line(
                custom_id=str(row.id),
                chunk_text=row.chunk_text,
                entity_type=row.entity_type,
                meeting_date=(
                    row.meeting_date.isoformat() if row.meeting_date else None
                ),
                state=doc_state_cache[row.document_id],
                model=self._model,
            )
            lines.append(serialize_batch_line(request))
        jsonl_bytes = ("\n".join(lines) + "\n").encode("utf-8")

        # Upload to S3.
        s3_key = (
            f"heatmap_ingest/batch_classification/"
            f"{_utcnow().strftime('%Y%m%d_%H%M%S')}/input.jsonl"
        )
        s3_url = await self._s3.upload_file_object(jsonl_bytes, s3_key)

        # Submit to OpenAI Batch API.
        # The input file must be uploaded via the OpenAI Files API first.
        file_obj = await self._client.files.create(
            file=("input.jsonl", io.BytesIO(jsonl_bytes), "application/jsonl"),
            purpose="batch",
        )
        batch = await self._client.batches.create(
            input_file_id=file_obj.id,
            endpoint="/v1/chat/completions",
            completion_window="24h",
            metadata={
                "purpose": "heatmap_chunk_classification",
                "chunk_count": str(len(pending_rows)),
                "input_s3_key": s3_key,
            },
        )

        # Persist the job row.
        job = BatchClassificationJob(
            batch_id=batch.id,
            input_jsonl_s3_key=s3_key,
            chunk_count=len(pending_rows),
            status=batch.status or "submitted",
            submitted_at=_utcnow(),
        )
        db.add(job)

        # Flip pending rows to 'submitted' and stamp the batch_id.
        for row in pending_rows:
            row.status = "submitted"
            row.batch_id = batch.id

        await db.commit()
        logger.info(
            f"Submitted batch {batch.id} ({len(pending_rows)} chunks, "
            f"input file {file_obj.id}, s3 {s3_url})"
        )
        return job

    async def _submit_pending_via_direct_api(
        self, db: AsyncSession
    ) -> BatchClassificationJob | None:
        """Classify pending chunks via concurrent chat-completions (OpenRouter)."""
        batch_size = int(getattr(settings, "HEATMAP_INGEST_BATCH_SIZE", 50_000))
        concurrency = int(getattr(settings, "OPENROUTER_BATCH_CONCURRENCY", 10))

        pending_rows = (
            (
                await db.execute(
                    select(PendingClassification)
                    .where(PendingClassification.status == "pending")
                    .order_by(PendingClassification.id)
                    .limit(batch_size)
                )
            )
            .scalars()
            .all()
        )
        if not pending_rows:
            logger.info("No pending classifications to submit.")
            return None

        batch_id = f"direct-{_utcnow().strftime('%Y%m%d_%H%M%S')}"
        logger.info(
            "Classifying %s chunks via OpenRouter direct API (concurrency=%s)",
            len(pending_rows),
            concurrency,
        )

        from app.services.vector_store.factory import (
            VectorStoreFactory,
            VectorStoreType,
        )

        vector_store = VectorStoreFactory.create(
            VectorStoreType(settings.VECTOR_STORE_TYPE)
        )
        tenant_cache: dict[int, int] = {}
        doc_state_cache: dict[int, str | None] = {}
        per_doc_classifications: dict[
            int, list[tuple[PendingClassification, ChunkClassification]]
        ] = {}
        stats = {"applied": 0, "failed": 0}
        semaphore = asyncio.Semaphore(concurrency)

        async def _classify_row(row: PendingClassification) -> None:
            async with semaphore:
                if row.document_id not in doc_state_cache:
                    doc_state_cache[row.document_id] = await self._state_for_doc(
                        db, row.document_id
                    )
                request = build_batch_request_line(
                    custom_id=str(row.id),
                    chunk_text=row.chunk_text,
                    entity_type=row.entity_type,
                    meeting_date=(
                        row.meeting_date.isoformat() if row.meeting_date else None
                    ),
                    state=doc_state_cache[row.document_id],
                    model=self._model,
                )
                body = dict(request["body"])
                body["model"] = normalize_model_name(body["model"])
                try:
                    response = await self._client.chat.completions.create(**body)
                    content = response.choices[0].message.content or "{}"
                    classification = ChunkClassification.model_validate(
                        json.loads(content)
                    )
                except Exception as exc:  # noqa: BLE001
                    row.status = "failed"
                    row.error_message = str(exc)[:1000]
                    stats["failed"] += 1
                    return

                try:
                    if row.document_id not in tenant_cache:
                        tenant_cache[row.document_id] = (
                            await self._tenant_id_for_doc(db, row.document_id)
                        )
                    tenant_id = tenant_cache[row.document_id]
                    if hasattr(vector_store, "update_metadata"):
                        await vector_store.update_metadata(
                            chunk_ids=[row.qdrant_point_id],
                            metadata=self._build_payload_metadata(
                                classification, row_entity_type=row.entity_type
                            ),
                            tenant_id=tenant_id,
                        )
                except Exception as exc:  # noqa: BLE001
                    row.status = "failed"
                    row.error_message = f"qdrant set_payload: {exc}"[:1000]
                    stats["failed"] += 1
                    return

                per_doc_classifications.setdefault(row.document_id, []).append(
                    (row, classification)
                )
                row.status = "applied"
                row.batch_id = batch_id
                row.error_message = None
                stats["applied"] += 1

                # A9 safety-net signal: a keyword flag fired but the
                # classifier produced no topic_tags. Logged (not actioned)
                # so eval can spot classifier misses during spot-check.
                if not classification.topic_tags and row.chunk_text:
                    self._log_keyword_safety_net(
                        row.chunk_text,
                        doc_state_cache.get(row.document_id),
                        row.id,
                    )

        await asyncio.gather(*[_classify_row(row) for row in pending_rows])
        await self._upsert_heatmap_aggregate(db, per_doc_classifications)

        job = BatchClassificationJob(
            batch_id=batch_id,
            # Column is NOT NULL; OpenRouter direct path has no JSONL upload.
            input_jsonl_s3_key=f"local://{batch_id}",
            chunk_count=len(pending_rows),
            status="applied",
            submitted_at=_utcnow(),
            applied_at=_utcnow(),
        )
        db.add(job)
        await db.commit()
        logger.info(
            "OpenRouter direct classification %s: %s applied, %s failed",
            batch_id,
            stats["applied"],
            stats["failed"],
        )
        return job

    # ------------------------------------------------------------------
    # 2. Poll
    # ------------------------------------------------------------------

    async def poll_batch(self, db: AsyncSession, batch_id: str) -> BatchClassificationJob:
        """Refresh a batch job's status from OpenAI."""
        job = (
            await db.execute(
                select(BatchClassificationJob).where(
                    BatchClassificationJob.batch_id == batch_id
                )
            )
        ).scalar_one_or_none()
        if job is None:
            raise ValueError(f"No BatchClassificationJob for batch_id={batch_id!r}")

        batch = await self._client.batches.retrieve(batch_id)
        job.status = batch.status or job.status
        if batch.status in ("completed", "failed", "expired", "cancelled"):
            job.completed_at = _utcnow()
            if getattr(batch, "errors", None):
                errs = batch.errors.data or [] if hasattr(batch.errors, "data") else []
                job.error_message = "; ".join(
                    (getattr(e, "message", "") or "") for e in errs
                ) or None
        await db.commit()
        logger.info(f"Batch {batch_id} status: {job.status}")
        return job

    # ------------------------------------------------------------------
    # 3. Apply results
    # ------------------------------------------------------------------

    async def apply_batch_results(
        self, db: AsyncSession, batch_id: str
    ) -> dict[str, int]:
        """
        Download the output JSONL for a completed batch, write the
        classification results to Qdrant (per-point set_payload), upsert
        into heatmap_aggregate, and flip pending rows to 'applied'.

        Returns a small stats dict.
        """
        job = await self.poll_batch(db, batch_id)
        if job.status != "completed":
            raise RuntimeError(
                f"Batch {batch_id} is not completed (status={job.status}). "
                f"Cannot apply results."
            )

        # Download the output file from OpenAI.
        batch = await self._client.batches.retrieve(batch_id)
        output_file_id = getattr(batch, "output_file_id", None)
        if not output_file_id:
            raise RuntimeError(f"Batch {batch_id} has no output_file_id.")

        file_response = await self._client.files.content(output_file_id)
        output_text = await file_response.aread() if hasattr(file_response, "aread") else file_response.read()
        if isinstance(output_text, bytes):
            output_text = output_text.decode("utf-8")

        # Cache all pending rows for this batch keyed by custom_id (which
        # we set to the pending_classification.id as a string).
        pending_rows = {
            str(r.id): r
            for r in (
                await db.execute(
                    select(PendingClassification).where(
                        PendingClassification.batch_id == batch_id
                    )
                )
            ).scalars().all()
        }
        if not pending_rows:
            logger.warning(f"No pending rows found for batch {batch_id}; nothing to apply.")
            return {"applied": 0, "skipped": 0, "failed": 0}

        # Group results by document for aggregate upsert efficiency.
        per_doc_classifications: dict[int, list[tuple[PendingClassification, ChunkClassification]]] = {}
        stats = {"applied": 0, "skipped": 0, "failed": 0}

        # Create a single vector store instance for the whole apply run.
        from app.services.vector_store.factory import (
            VectorStoreFactory,
            VectorStoreType,
        )

        vector_store = VectorStoreFactory.create(
            VectorStoreType(settings.VECTOR_STORE_TYPE)
        )

        # Cache tenant_id per document_id to avoid re-querying.
        tenant_cache: dict[int, int] = {}

        # First parse all lines.
        for line in output_text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                result = json.loads(line)
            except json.JSONDecodeError as exc:
                logger.warning(f"Skipping unparseable batch output line: {exc}")
                stats["failed"] += 1
                continue

            custom_id = result.get("custom_id")
            pending = pending_rows.get(custom_id)
            if pending is None:
                stats["skipped"] += 1
                continue

            # The batch response wraps the chat completion in
            # `response.body.choices[0].message.content`.
            error = result.get("error")
            if error:
                pending.status = "failed"
                pending.error_message = json.dumps(error)[:1000]
                stats["failed"] += 1
                continue

            try:
                body = result["response"]["body"]
                content = body["choices"][0]["message"]["content"]
                payload = json.loads(content)
                classification = ChunkClassification.model_validate(payload)
            except Exception as exc:  # noqa: BLE001
                pending.status = "failed"
                pending.error_message = f"parse error: {exc}"[:1000]
                stats["failed"] += 1
                continue

            # Write the classification fields to the Qdrant point.
            try:
                if pending.document_id not in tenant_cache:
                    tenant_cache[pending.document_id] = (
                        await self._tenant_id_for_doc(db, pending.document_id)
                    )
                tenant_id = tenant_cache[pending.document_id]
                if hasattr(vector_store, "update_metadata"):
                    await vector_store.update_metadata(
                        chunk_ids=[pending.qdrant_point_id],
                        metadata=self._build_payload_metadata(
                            classification, row_entity_type=pending.entity_type
                        ),
                        tenant_id=tenant_id,
                    )
            except Exception as exc:  # noqa: BLE001
                logger.error(
                    f"Failed to set_payload for point {pending.qdrant_point_id}: {exc}",
                    exc_info=True,
                )
                pending.status = "failed"
                pending.error_message = f"qdrant set_payload: {exc}"[:1000]
                stats["failed"] += 1
                continue

            # Buffer for the aggregate upsert.
            per_doc_classifications.setdefault(pending.document_id, []).append(
                (pending, classification)
            )

            pending.status = "applied"
            pending.error_message = None
            stats["applied"] += 1

            # A9 safety-net signal: a keyword flag fired but the
            # classifier produced no topic_tags. Logged (not actioned)
            # so eval can spot classifier misses during spot-check.
            if not classification.topic_tags and pending.chunk_text:
                self._log_keyword_safety_net(
                    pending.chunk_text,
                    # state lookup deferred to avoid an extra DB hit per
                    # chunk; the per-call doc_state cache isn't available
                    # in the apply path. Pass None and the helper resolves
                    # via the document lookup if needed.
                    None,
                    pending.id,
                )

        # Upsert heatmap_aggregate per (school, topic).
        await self._upsert_heatmap_aggregate(db, per_doc_classifications)

        # Persist output S3 key on the job row.
        output_s3_key = (
            f"heatmap_ingest/batch_classification/"
            f"{batch_id}/output.jsonl"
        )
        try:
            await self._s3.upload_file_object(
                output_text.encode("utf-8"), output_s3_key
            )
            job.output_jsonl_s3_key = output_s3_key
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"Failed to mirror batch output to S3: {exc}")
        job.status = "applied"
        job.applied_at = _utcnow()

        await db.commit()
        logger.info(
            f"Applied batch {batch_id}: "
            f"{stats['applied']} applied, {stats['skipped']} skipped, "
            f"{stats['failed']} failed"
        )
        return stats

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _build_payload_metadata(
        self,
        classification: ChunkClassification,
        *,
        row_entity_type: str | None,
    ) -> dict[str, Any]:
        """Build the Qdrant set_payload dict for one chunk classification.

        V1 additions (spec A3/A5): topic_tags, action_stage, speakers.
        The legacy topics / action_types / subtopics fields are preserved
        for backward compatibility with the heatmap_aggregate roll-up and
        the existing retrieval path; do NOT drop until the follow-up
        migration.
        """
        return {
            # Legacy fields (backward compat).
            "topics": classification.topics,
            "action_types": classification.action_types,
            "subtopics": classification.subtopics,
            "evidence_quote": classification.evidence_quote,
            "off_topic": classification.off_topic,
            "entity_type": row_entity_type,
            "classified": True,
            "classified_at": _utcnow().isoformat(),
            # V1 fields (spec A3/A5).
            "topic_tags": [
                {"category": t.category, "subtopic": t.subtopic}
                for t in classification.topic_tags
            ],
            "action_stage": classification.action_stage,
            "speakers": [
                {"name": s.name, "role": s.role}
                for s in classification.speakers
            ],
        }

    def _log_keyword_safety_net(
        self,
        chunk_text: str,
        state: str | None,
        pending_id: int | str,
    ) -> None:
        """Log A9 safety-net events: keyword flag fired but topic_tags empty.

        Reconciles the pre-computed `keyword_flags` (from ingest) against
        the classifier output. Per A9 this is expected and useful — the
        safety net for classifier misses. We log so eval can spot misses
        during spot-check; we do NOT mutate the classification.
        """
        try:
            from app.services.heatmap_ingest.vocabulary_packs import (
                get_keyword_flags_for_state,
                match_keyword_flags,
            )

            flags = match_keyword_flags(
                chunk_text, get_keyword_flags_for_state(state)
            )
            if flags:
                logger.info(
                    "A9 safety-net: pending_id=%s state=%s keyword_flags=%s "
                    "but topic_tags=[]",
                    pending_id,
                    state,
                    flags,
                )
        except Exception:  # noqa: BLE001
            # Reconciliation is best-effort; never fail a classification on it.
            pass

    async def _state_for_doc(
        self, db: AsyncSession, document_id: int
    ) -> str | None:
        """Look up the state (2-letter) for a document (cached per call)."""
        from app.models.documents import Document

        doc = await db.get(Document, document_id)
        if doc is None:
            return None
        return doc.state

    async def _tenant_id_for_doc(
        self, db: AsyncSession, document_id: int
    ) -> int:
        """Look up tenant_id for a document (cached per call)."""
        from app.models.documents import Document

        doc = await db.get(Document, document_id)
        if doc is None:
            raise ValueError(f"Document {document_id} not found")
        return doc.tenant_id

    async def _upsert_heatmap_aggregate(
        self,
        db: AsyncSession,
        per_doc_classifications: dict[int, list[tuple[PendingClassification, ChunkClassification]]],
    ) -> None:
        """
        For each (document, topic) pair, increment heatmap_aggregate.

        Aggregation rules per the plan:
          - chunk_count: +1 per chunk with that topic
          - doc_count: +1 per (doc, topic) pair (once per doc)
          - meeting_count: +1 per distinct meeting_date within that doc
            (a doc has one meeting_date, so this is 0 or 1)
          - last_meeting_date: max meeting_date seen
          - action_types: increment counts per action_type seen
        """
        # Look up source_id (school_id) per document. We join through
        # scraped_media → schools to get the school row id, since the
        # heatmap is one pin per charter school.
        from app.models.documents import Document
        from app.models.school import ScrapedMedia

        doc_to_school: dict[int, int | None] = {}
        for doc_id in per_doc_classifications.keys():
            doc = await db.get(Document, doc_id)
            if doc is None or not doc.source_metadata:
                doc_to_school[doc_id] = None
                continue
            sm_id = doc.source_metadata.get("scraped_media_id")
            if not sm_id:
                doc_to_school[doc_id] = None
                continue
            sm = await db.get(ScrapedMedia, sm_id)
            if sm is None:
                doc_to_school[doc_id] = None
                continue
            doc_to_school[doc_id] = sm.school_id

        # Walk each (doc, topic) and upsert.
        for doc_id, classifications in per_doc_classifications.items():
            school_id = doc_to_school.get(doc_id)
            if school_id is None:
                continue

            # Aggregate per topic within this doc.
            doc_topic_chunks: dict[str, int] = {}
            doc_topic_actions: dict[str, dict[str, int]] = {}
            doc_meeting_date: datetime | None = None

            for pending, classification in classifications:
                if classification.off_topic:
                    continue
                # Pull the doc-level meeting_date from the pending row.
                if pending.meeting_date and not doc_meeting_date:
                    doc_meeting_date = pending.meeting_date
                for topic in classification.topics:
                    doc_topic_chunks[topic] = doc_topic_chunks.get(topic, 0) + 1
                    if topic not in doc_topic_actions:
                        doc_topic_actions[topic] = {}
                    for action in classification.action_types:
                        doc_topic_actions[topic][action] = (
                            doc_topic_actions[topic].get(action, 0) + 1
                        )

            for topic, chunk_count in doc_topic_chunks.items():
                await self._upsert_aggregate_row(
                    db,
                    source_id=school_id,
                    topic=topic,
                    chunk_delta=chunk_count,
                    doc_delta=1,
                    meeting_delta=1 if doc_meeting_date else 0,
                    meeting_date=doc_meeting_date,
                    action_counts=doc_topic_actions.get(topic, {}),
                )

    async def _upsert_aggregate_row(
        self,
        db: AsyncSession,
        *,
        source_id: int,
        topic: str,
        chunk_delta: int,
        doc_delta: int,
        meeting_delta: int,
        meeting_date: datetime | None,
        action_counts: dict[str, int],
    ) -> None:
        """Upsert one (school, topic) row in heatmap_aggregate."""
        existing = (
            await db.execute(
                select(HeatmapAggregate).where(
                    HeatmapAggregate.source_id == source_id,
                    HeatmapAggregate.topic == topic,
                )
            )
        ).scalar_one_or_none()

        if existing is None:
            # New row.
            existing = HeatmapAggregate(
                source_id=source_id,
                topic=topic,
                chunk_count=chunk_delta,
                doc_count=doc_delta,
                meeting_count=meeting_delta,
                last_meeting_date=meeting_date,
                action_types=action_counts,
            )
            db.add(existing)
        else:
            existing.chunk_count += chunk_delta
            existing.doc_count += doc_delta
            existing.meeting_count += meeting_delta
            if meeting_date:
                if existing.last_meeting_date is None or meeting_date > existing.last_meeting_date:
                    existing.last_meeting_date = meeting_date
            # Merge action counts.
            existing_actions = dict(existing.action_types or {})
            for action, count in action_counts.items():
                existing_actions[action] = existing_actions.get(action, 0) + count
            existing.action_types = existing_actions

        await db.flush()
