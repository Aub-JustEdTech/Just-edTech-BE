"""
Heatmap Generation Engine service.

Counts Qdrant chunk instances per district, filtered by V1 `topic_tags`
categories and academic-year-based timeframe presets, with a citation
drill-down per single district. Reads directly from the vector store
(never the `heatmap_aggregate` table).
"""

from __future__ import annotations

import asyncio
import logging
import math
from datetime import date
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.connector import AsyncSessionLocal
from app.models.documents import Document
from app.models.school import School
from app.schemas.heatmap_engine import (
    CitationSort,
    DistrictCitationsEngineResponse,
    DistrictCountItem,
    DistrictCountResponse,
    EngineCitationItem,
    TimeframePreset,
    TopicCategory,
)
from app.services.heatmap_engine.timeframe import (
    build_date_range_filter,
    build_timeframe_filter,
)
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

# Upper bound on in-flight Qdrant `count` calls when resolving the per-category
# breakdown. Counts are cheap but each is a round trip, so a modest fan-out
# keeps the request responsive without hammering the vector store.
_COUNT_CONCURRENCY = 8


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
        start_date: date | None = None,
        end_date: date | None = None,
    ) -> dict[str, Any]:
        """
        Assemble the engine-style filter fragments to hand to the vector store.

        Returns a dict with `must_match`, `must_match_any`,
        `nested_match_any`, and `range_match` keys ready to splat into
        `count_chunks` / `filter_chunks`.

        When both `start_date` and `end_date` are given, the custom
        date-range filter (a `range_match` on `meeting_date`) is used
        instead of the `timeframe` preset — the two are mutually
        exclusive, and an explicit range always wins.
        """
        must_match: dict[str, Any] = {"classified": True}
        if district_name:
            must_match[_DISTRICT_NAME_FIELD] = district_name

        must_match_any: dict[str, list] | None = None
        range_match: dict[str, dict[str, str]] | None = None
        if start_date and end_date:
            range_match = build_date_range_filter(start_date, end_date)
        else:
            # Timeframe → must_match_any fragment over quarter_month / school_year.
            must_match_any = build_timeframe_filter(timeframe) or None

        nested_match_any: dict[str, list] | None = None
        if categories:
            nested_match_any = {
                _TOPIC_TAGS_FIELD: [c.value for c in categories],
            }

        return {
            "must_match": must_match,
            "must_match_any": must_match_any,
            "nested_match_any": nested_match_any,
            "range_match": range_match,
        }

    async def count_by_district(
        self,
        *,
        tenant_id: int,
        timeframe: TimeframePreset,
        categories: list[TopicCategory],
        state: str = "MA",
        include_zero: bool = True,
        breakdown: bool = False,
        start_date: date | None = None,
        end_date: date | None = None,
    ) -> DistrictCountResponse:
        """
        Return one `DistrictCountItem` per active school for the tenant.

        For each school we issue a single Qdrant `count` call scoped to
        the district's name (the indexed payload field present on every
        ingested chunk), the timeframe buckets (or the custom date range
        when `start_date`/`end_date` are both given — see
        `_build_filter_fragments`), and the selected `topic_tags`
        categories (or all categories if none are supplied). MA has ~280
        districts, so ~280 count calls is well within sub-second budgets.

        With `breakdown=True` each data-bearing district additionally gets
        `top_category` / `top_category_count` / `category_counts` — the
        full per-category breakdown of the selected categories. This costs
        extra counts, so it is opt-in; see `_resolve_top_categories`.
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
                start_date=start_date,
                end_date=end_date,
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

        if breakdown:
            await self._resolve_top_categories(
                items,
                tenant_id=tenant_id,
                timeframe=timeframe,
                categories=categories,
                start_date=start_date,
                end_date=end_date,
            )

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
            start_date=start_date.isoformat() if start_date else None,
            end_date=end_date.isoformat() if end_date else None,
        )

    async def _resolve_top_categories(
        self,
        items: list[DistrictCountItem],
        *,
        tenant_id: int,
        timeframe: TimeframePreset,
        categories: list[TopicCategory],
        start_date: date | None = None,
        end_date: date | None = None,
    ) -> None:
        """
        Fill `top_category`, `top_category_count`, and `category_counts`
        on `items`, in place.

        Requires one extra count per (district, selected category). Unlike
        the original top-category-only version, there is no early exit
        once a category saturates `chunk_count` — the report's
        topic-mentions-per-district breakdown needs every category's
        count, not just the winner. Two cuts still keep the cost down:

        1. Districts with `chunk_count == 0` are skipped — nothing to rank.
        2. A single selected category needs no counts at all: it is the
           only candidate, and `chunk_count` is already its count.

        The probes run concurrently, capped at `_COUNT_CONCURRENCY`.
        """
        selected = list(categories) if categories else list(TopicCategory)
        active = [item for item in items if item.chunk_count > 0]
        if not active:
            return

        if len(selected) == 1:
            only = selected[0]
            for item in active:
                item.top_category = only
                item.top_category_count = item.chunk_count
                item.category_counts = {only: item.chunk_count}
            return

        vector_store = self._get_vector_store()
        semaphore = asyncio.Semaphore(_COUNT_CONCURRENCY)
        probe_calls = 0

        async def _resolve(item: DistrictCountItem) -> None:
            nonlocal probe_calls
            counts: dict[TopicCategory, int] = {}
            async with semaphore:
                # Iterate in `selected` order so ties on `max()` below settle
                # deterministically on the earliest-declared category.
                for category in selected:
                    fragments = self._build_filter_fragments(
                        district_name=item.district_name,
                        timeframe=timeframe,
                        categories=[category],
                        start_date=start_date,
                        end_date=end_date,
                    )
                    count = await vector_store.count_chunks(
                        tenant_id=tenant_id,
                        **fragments,
                    )
                    probe_calls += 1
                    if count:
                        counts[category] = count

            item.category_counts = counts
            if counts:
                top_category = max(counts, key=lambda c: counts[c])
                item.top_category = top_category
                item.top_category_count = counts[top_category]

        await asyncio.gather(*(_resolve(item) for item in active))

        logger.info(
            f"heatmap breakdown tenant={tenant_id} "
            f"timeframe={timeframe.value} "
            f"categories={[c.value for c in selected]} "
            f"active_districts={len(active)} probe_calls={probe_calls}"
        )

    # Bound on how many chunks `sort=date_desc` fetches before sorting.
    # Fetching more than this to find the true most-recent items would cost
    # an unbounded vector-store scan; in practice a single district's
    # matching chunk count comfortably fits under this cap.
    _REPORT_SORT_FETCH_CAP = 200

    async def get_district_citations(
        self,
        *,
        tenant_id: int,
        org_code: str,
        timeframe: TimeframePreset,
        categories: list[TopicCategory],
        page: int,
        page_size: int,
        start_date: date | None = None,
        end_date: date | None = None,
        sort: CitationSort = CitationSort.DEFAULT,
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

        `sort=date_desc` (used by the report export's "most recent
        snippets" section) fetches a larger bounded batch up front and
        sorts by `meeting_date` descending before paging, instead of the
        default vector-store return order — otherwise a high-activity
        district's true most-recent citations could sit past whatever
        page the default (unsorted) fetch happened to cover.
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
                start_date=start_date,
                end_date=end_date,
            )

            offset = (page - 1) * page_size

            if sort == CitationSort.DATE_DESC:
                chunks = await vector_store.filter_chunks(
                    tenant_id=tenant_id,
                    **fragments,
                    limit=self._REPORT_SORT_FETCH_CAP,
                )
                # Missing/unparseable dates sort last (oldest) rather than
                # erroring or floating to the top.
                chunks = sorted(
                    chunks,
                    key=lambda ch: ch.get("metadata", {}).get("meeting_date") or "",
                    reverse=True,
                )
            else:
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
            (
                await db.execute(
                    select(Document).where(
                        Document.tenant_id == tenant_id,
                        Document.doc_id.in_(valid),
                    )
                )
            )
            .scalars()
            .all()
        )
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
                logger.warning(f"Failed to presign S3 URL for doc_id={doc_id}: {exc}")
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
            meta.get("source_media_url") or source_meta.get("source_media_url") or ""
        )
        source_page_url = (
            meta.get("source_page_url") or source_meta.get("source_page_url") or ""
        )

        return EngineCitationItem(
            document_id=doc_id,
            document_title=(
                meta.get("document_name") or meta.get("school_name") or "Untitled"
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
            "total_pages": (max(1, math.ceil(total / page_size)) if page_size else 1),
        }


heatmap_engine_service = HeatmapEngineService()
