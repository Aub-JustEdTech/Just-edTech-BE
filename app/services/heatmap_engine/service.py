"""
Heatmap Generation Engine service.

Counts Qdrant chunk instances per district, filtered by V1 `topic_tags`
categories and academic-year-based timeframe presets, with a citation
drill-down per single district. Reads directly from the vector store
(never the `heatmap_aggregate` table).
"""

from __future__ import annotations

import logging
import math
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.connector import AsyncSessionLocal
from app.models.documents import Document
from app.models.school import School
from app.schemas.heatmap_engine import (
    DistrictCitationsEngineResponse,
    DistrictCountItem,
    DistrictCountResponse,
    EngineCitationItem,
    TimeframePreset,
    TopicCategory,
)
from app.services.heatmap_engine.timeframe import build_timeframe_filter
from app.services.vector_store.base import VectorStore
from app.services.vector_store.factory import VectorStoreFactory, VectorStoreType
from app.utils.s3 import S3Manager

logger = logging.getLogger(__name__)

# Map DB district_type values to the API's two-bucket normalized form.
_CHARTER_TYPES = {"charter district", "charter"}


def _normalize_district_type(raw: str | None) -> str:
    if raw and raw.strip().lower() in _CHARTER_TYPES:
        return "charter"
    return "public"


# Qdrant payload field holding the V1 `topic_tags` array of
# `{category, subtopic}` objects written by the batch classifier.
_TOPIC_TAGS_FIELD = "topic_tags"

# Qdrant payload field holding the school/district name. We filter by
# `district_name` rather than `school_id` because the legacy ingest path
# (pre-V1) populates `district_name` on every chunk but does not always
# write `school_id`. `district_name` is indexed as a KEYWORD payload
# field, so equality filters on it are fast.
_DISTRICT_NAME_FIELD = "district_name"


class HeatmapEngineService:
    """Vector-store-backed chunk-instance counter for the heatmap engine."""

    def _get_vector_store(self) -> VectorStore:
        return VectorStoreFactory.create(VectorStoreType(settings.VECTOR_STORE_TYPE))

    def _build_filter_fragments(
        self,
        *,
        district_name: str | None,
        timeframe: TimeframePreset,
        categories: list[TopicCategory],
    ) -> dict[str, Any]:
        """
        Assemble the engine-style filter fragments to hand to the vector store.

        Returns a dict with `must_match`, `must_match_any`, and
        `nested_match_any` keys ready to splat into `count_chunks` /
        `filter_chunks`.
        """
        must_match: dict[str, Any] = {"classified": True}
        if district_name:
            must_match[_DISTRICT_NAME_FIELD] = district_name

        # Timeframe → must_match_any fragment over quarter_month / school_year.
        timeframe_fragment = build_timeframe_filter(timeframe)

        nested_match_any: dict[str, list] | None = None
        if categories:
            nested_match_any = {
                _TOPIC_TAGS_FIELD: [c.value for c in categories],
            }

        return {
            "must_match": must_match,
            "must_match_any": timeframe_fragment or None,
            "nested_match_any": nested_match_any,
        }

    async def count_by_district(
        self,
        *,
        tenant_id: int,
        timeframe: TimeframePreset,
        categories: list[TopicCategory],
        state: str = "MA",
        include_zero: bool = True,
    ) -> DistrictCountResponse:
        """
        Return one `DistrictCountItem` per active school for the tenant.

        For each school we issue a single Qdrant `count` call scoped to
        the district's name (the indexed payload field present on every
        ingested chunk), the timeframe buckets, and the selected
        `topic_tags` categories (or all categories if none are supplied).
        MA has ~280 districts, so ~280 count calls is well within
        sub-second budgets.
        """
        vector_store = self._get_vector_store()

        async with AsyncSessionLocal() as db:
            schools = await self._list_schools(db, tenant_id, state)

        items: list[DistrictCountItem] = []
        total_chunks = 0
        for school in schools:
            fragments = self._build_filter_fragments(
                district_name=school.name,
                timeframe=timeframe,
                categories=categories,
            )
            count = await vector_store.count_chunks(
                tenant_id=tenant_id,
                **fragments,
            )
            if count == 0 and not include_zero:
                continue
            items.append(
                DistrictCountItem(
                    org_code=school.org_code,
                    district_name=school.name,
                    district_type=_normalize_district_type(school.district_type),
                    state=school.state or state,
                    chunk_count=count,
                )
            )
            total_chunks += count

        # Sort: charter first (drives the map), then by chunk_count desc.
        items.sort(
            key=lambda i: (
                0 if i.district_type == "charter" else 1,
                -i.chunk_count,
            )
        )

        return DistrictCountResponse(
            timeframe=timeframe,
            categories=categories,
            total_districts=len(items),
            total_chunks=total_chunks,
            districts=items,
        )

    async def get_district_citations(
        self,
        *,
        tenant_id: int,
        org_code: str,
        timeframe: TimeframePreset,
        categories: list[TopicCategory],
        page: int,
        page_size: int,
    ) -> tuple[DistrictCitationsEngineResponse, dict]:
        """
        Return paginated chunk citations for one district + filter set.

        District is resolved by `org_code` (tenant-scoped). Mirrors the
        prior citations pattern (fetch enough to cover the requested
        page, paginate client-side, hydrate each chunk into a citation
        item) but using the engine's filter fragments and response shape.
        The S3 URL for each chunk's source document is looked up from
        the `documents` table via `doc_id == chunk.document_id` and
        returned as `s3_url`. `source_url` / `source_page_url` prefer
        Qdrant payload fields written at ingest, with a
        Document.source_metadata fallback until existing points are
        backfilled.
        """
        vector_store = self._get_vector_store()

        async with AsyncSessionLocal() as db:
            school = await self._find_school_by_org_code(db, tenant_id, org_code)

            if school is None:
                logger.info(
                    f"No school found for org_code={org_code!r}; "
                    "returning empty citations"
                )
                return self._empty_response(
                    org_code, timeframe, categories
                ), self._meta(page, page_size, 0)

            fragments = self._build_filter_fragments(
                district_name=school.name,
                timeframe=timeframe,
                categories=categories,
            )

            offset = (page - 1) * page_size
            fetch_limit = offset + page_size
            chunks = await vector_store.filter_chunks(
                tenant_id=tenant_id,
                **fragments,
                limit=fetch_limit,
            )
            page_chunks = chunks[offset : offset + page_size]

            # Batch-fetch Document rows for the documents referenced by
            # this page so we can generate openable presigned S3 URLs
            # and fall back source URLs until Qdrant payloads are backfilled.
            doc_ids = {
                ch.get("metadata", {}).get("document_id")
                for ch in page_chunks
                if ch.get("metadata", {}).get("document_id")
            }
            docs_by_id = await self._fetch_documents(db, tenant_id, doc_ids)

            # Generate presigned S3 URLs (one per distinct document).
            s3_url_by_doc_id = await self._presign_document_urls(docs_by_id)

            citations = [
                self._hydrate_citation(ch, s3_url_by_doc_id, docs_by_id)
                for ch in page_chunks
            ]

        total = len(chunks)
        response = DistrictCitationsEngineResponse(
            org_code=school.org_code,
            district_name=school.name,
            timeframe=timeframe,
            categories=categories,
            citations=citations,
        )
        return response, self._meta(page, page_size, total)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _list_schools(
        self, db: AsyncSession, tenant_id: int, state: str
    ) -> list[School]:
        """Active schools for the tenant, optionally state-scoped."""
        stmt = select(School).where(
            School.tenant_id == tenant_id,
            School.is_active.is_(True),
        )
        if state:
            stmt = stmt.where(School.state == state)
        stmt = stmt.order_by(School.name)
        result = await db.execute(stmt)
        return list(result.scalars().all())

    async def _find_school_by_org_code(
        self, db: AsyncSession, tenant_id: int, org_code: str
    ) -> School | None:
        stmt = (
            select(School)
            .where(
                School.tenant_id == tenant_id,
                School.org_code == org_code,
            )
            .limit(1)
        )
        return (await db.execute(stmt)).scalar_one_or_none()

    @staticmethod
    async def _fetch_documents(
        db: AsyncSession, tenant_id: int, doc_ids: set[str | None]
    ) -> dict[str, Document]:
        """
        Look up `Document` rows for each `doc_id` in `doc_ids`.

        Qdrant chunk payloads store `document_id` as the string `doc_uuid`
        (e.g. `school-07530000-...`), which matches `documents.doc_id`.
        Returns a `{doc_id: Document}` mapping; doc_ids with no row are
        omitted.
        """
        valid = [d for d in doc_ids if d]
        if not valid:
            return {}
        rows = (
            await db.execute(
                select(Document).where(
                    Document.tenant_id == tenant_id,
                    Document.doc_id.in_(valid),
                )
            )
        ).scalars().all()
        return {doc.doc_id: doc for doc in rows if doc.doc_id}

    @staticmethod
    def _build_s3_manager() -> S3Manager:
        return S3Manager(
            bucket_name=settings.S3_BUCKET_NAME,
            region_name=settings.S3_REGION,
            aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
            aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
        )

    # MIME map used to set the presigned URL's `ResponseContentType` for
    # inline browser rendering (mirrors the documents presigned-url path).
    _MIME_BY_EXT = {
        ".pdf": "application/pdf",
        ".md": "text/markdown; charset=utf-8",
        ".txt": "text/plain; charset=utf-8",
        ".doc": "application/msword",
        ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ".xls": "application/vnd.ms-excel",
        ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    }

    async def _presign_document_urls(
        self, docs_by_id: dict[str, Document]
    ) -> dict[str, str]:
        """
        Generate an openable presigned HTTPS URL for each document's S3
        object. PDFs get a `#page=N` anchor appended when a page number is
        known — but since the per-chunk page number lives on the chunk
        (not the document), the page anchor is added later in
        `_hydrate_citation`; here we return the bare presigned URL keyed
        by `doc_id`. Returns `{}` if S3 isn't configured or generation
        fails (caller falls back to None).
        """
        if not docs_by_id:
            return {}

        prefix = f"s3://{settings.S3_BUCKET_NAME}/"
        s3_manager = self._build_s3_manager()

        result: dict[str, str] = {}
        for doc_id, doc in docs_by_id.items():
            raw = doc.s3_url
            if not raw or not raw.startswith(prefix):
                continue
            s3_key = raw[len(prefix) :]
            ext = (doc.document_type or "").lower()
            content_type = self._MIME_BY_EXT.get(ext, "application/octet-stream")
            safe_name = (doc.name or f"document_{doc.id}").replace('"', "")
            content_disposition = f'inline; filename="{safe_name}"'
            try:
                url = await s3_manager.get_presigned_url(
                    s3_key=s3_key,
                    expiration=3600,
                    http_method="GET",
                    response_content_type=content_type,
                    response_content_disposition=content_disposition,
                )
                result[doc_id] = url
            except Exception as exc:
                logger.warning(
                    f"Failed to presign S3 URL for doc_id={doc_id}: {exc}"
                )
        return result

    @staticmethod
    def _hydrate_citation(
        chunk: dict[str, Any],
        s3_url_by_doc_id: dict[str, str] | None = None,
        docs_by_id: dict[str, Document] | None = None,
    ) -> EngineCitationItem:
        meta = chunk.get("metadata", {}) or {}
        text = chunk.get("text", "") or ""
        snippet = text[:300] + ("…" if len(text) > 300 else "")
        topic_tags = meta.get("topic_tags") or []
        if not isinstance(topic_tags, list):
            topic_tags = []
        doc_id = meta.get("document_id")
        base_s3 = (s3_url_by_doc_id or {}).get(doc_id) if doc_id else None
        # Append a `#page=N` anchor for PDFs so the browser jumps to the
        # specific page the chunk came from.
        s3_url: str | None = None
        if base_s3:
            page_number = meta.get("page_number")
            doc_type = (meta.get("document_type") or "").lower()
            if doc_type == ".pdf" and page_number:
                s3_url = f"{base_s3}#page={page_number}"
            else:
                s3_url = base_s3

        # Prefer denormalized Qdrant payload; fall back to Document
        # source_metadata for points ingested before these fields were
        # written at vector-store time.
        source_meta: dict[str, Any] = {}
        if doc_id and docs_by_id and doc_id in docs_by_id:
            source_meta = docs_by_id[doc_id].source_metadata or {}
        source_url = (
            meta.get("source_media_url")
            or source_meta.get("source_media_url")
            or ""
        )
        source_page_url = (
            meta.get("source_page_url")
            or source_meta.get("source_page_url")
            or ""
        )

        return EngineCitationItem(
            document_id=doc_id,
            document_title=(
                meta.get("document_name")
                or meta.get("school_name")
                or "Untitled"
            ),
            date=meta.get("meeting_date"),
            snippet=snippet,
            source_url=source_url,
            source_page_url=source_page_url,
            s3_url=s3_url,
            page_number=meta.get("page_number"),
            topic_tags=topic_tags,
        )

    @staticmethod
    def _empty_response(
        org_code: str,
        timeframe: TimeframePreset,
        categories: list[TopicCategory],
    ) -> DistrictCitationsEngineResponse:
        return DistrictCitationsEngineResponse(
            org_code=org_code,
            district_name="",
            timeframe=timeframe,
            categories=categories,
            citations=[],
        )

    @staticmethod
    def _meta(page: int, page_size: int, total: int) -> dict:
        return {
            "page": page,
            "page_size": page_size,
            "total": total,
            "total_pages": (
                max(1, math.ceil(total / page_size)) if page_size else 1
            ),
        }


heatmap_engine_service = HeatmapEngineService()
