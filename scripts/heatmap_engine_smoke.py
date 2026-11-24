"""
Smoke test for the Heatmap Generation Engine.

Runs inside the just-edtech api container (or anywhere with the venv +
env vars) against the live Qdrant + Postgres. Picks the first tenant
that has a _documents collection, runs count_by_district for the `lgbtq`
category + a wide timeframe, then cross-checks one district against a
raw Qdrant scroll + len sanity check, and finally exercises the
per-district citations endpoint logic.
"""

import asyncio
import logging

from sqlalchemy import select

from app.core.config import settings
from app.db.connector import AsyncSessionLocal
from app.models.school import School
from app.schemas.heatmap_engine import TimeframePreset, TopicCategory
from app.services.heatmap_engine import heatmap_engine_service
from app.services.heatmap_engine.service import _DISTRICT_NAME_FIELD
from app.services.heatmap_engine.timeframe import build_timeframe_filter
from app.services.vector_store.factory import VectorStoreFactory, VectorStoreType

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("smoke")


async def main() -> None:
    # Pick a district known to have data: scan active MA schools and use
    # the first one whose `district_name` matches a chunk in Qdrant with
    # the lgbtq category within the 3-year window we'll query.
    vs_probe = VectorStoreFactory.create(VectorStoreType(settings.VECTOR_STORE_TYPE))
    async with AsyncSessionLocal() as db:
        all_schools = list(
            (
                await db.execute(
                    select(School)
                    .where(School.is_active.is_(True), School.state == "MA")
                    .order_by(School.name)
                )
            )
            .scalars()
            .all()
        )

    school_row = None
    tf_fragment_probe = build_timeframe_filter(TimeframePreset.THREE_YEARS)
    for s in all_schools:
        probe = await vs_probe.filter_chunks(
            tenant_id=s.tenant_id,
            must_match={_DISTRICT_NAME_FIELD: s.name},
            must_match_any=tf_fragment_probe or None,
            nested_match_any={"topic_tags": [TopicCategory.LGBTQ.value]},
            limit=1,
        )
        if probe:
            school_row = s
            log.info(
                f"Found data-bearing district: {s.name!r} "
                f"(tenant {s.tenant_id}) after probing"
            )
            break

    if school_row is None:
        log.error("No MA school has LGBTQ-tagged chunks in Qdrant (3-year window).")
        return

    tenant_id = school_row.tenant_id
    target_name = school_row.name
    target_org_code = school_row.org_code
    log.info(
        f"Smoke target: tenant_id={tenant_id} school={target_name!r} "
        f"org_code={target_org_code!r}"
    )

    # 1) count_by_district for lgbtq + 3_years (broad enough to catch data).
    response = await heatmap_engine_service.count_by_district(
        tenant_id=tenant_id,
        timeframe=TimeframePreset.THREE_YEARS,
        categories=[TopicCategory.LGBTQ],
        state="MA",
        include_zero=False,
    )
    log.info(
        f"count_by_district: total_districts={response.total_districts} "
        f"total_chunks={response.total_chunks}"
    )
    top = response.districts[:5]
    for d in top:
        log.info(
            f"  district={d.district_name!r} type={d.district_type} "
            f"count={d.chunk_count}"
        )

    # 2) Cross-check: scroll the SAME filter directly and compare len.
    # Same timeframe fragment so the equality is exact.
    vs = VectorStoreFactory.create(VectorStoreType(settings.VECTOR_STORE_TYPE))
    tf_fragment = build_timeframe_filter(TimeframePreset.THREE_YEARS)
    sanity = await vs.filter_chunks(
        tenant_id=tenant_id,
        must_match={
            "classified": True,
            _DISTRICT_NAME_FIELD: target_name,
        },
        must_match_any=tf_fragment or None,
        nested_match_any={"topic_tags": [TopicCategory.LGBTQ.value]},
        limit=100_000,
    )
    log.info(
        f"Sanity scroll: {len(sanity)} chunks for {target_name!r} "
        f"(lgbtq, 3-year window)"
    )

    # Find the matching district in the engine response.
    match = next(
        (d for d in response.districts if d.district_name == target_name),
        None,
    )
    if match is None:
        log.error(
            f"Target district {target_name!r} not in response even though "
            f"probe found data — engine response is wrong."
        )
        return
    assert match.chunk_count == len(sanity), (
        f"engine count {match.chunk_count} != sanity scroll len {len(sanity)}"
    )
    log.info(
        f"OK: engine count {match.chunk_count} == sanity scroll len {len(sanity)}"
    )
    # Verify org_code is populated on the matching district.
    assert match.org_code, f"org_code missing on district {match.district_name!r}"
    log.info(f"OK: org_code={match.org_code!r} for {match.district_name!r}")

    # 3) Per-district citations for the same school (keyed by org_code).
    citations, meta = await heatmap_engine_service.get_district_citations(
        tenant_id=tenant_id,
        org_code=target_org_code,
        timeframe=TimeframePreset.THREE_YEARS,
        categories=[TopicCategory.LGBTQ],
        page=1,
        page_size=5,
    )
    log.info(
        f"citations: district={citations.district_name!r} "
        f"org_code={citations.org_code!r} "
        f"returned={len(citations.citations)} meta={meta}"
    )
    assert citations.org_code == match.org_code, (
        f"org_code mismatch: response={citations.org_code} vs count={match.org_code}"
    )
    log.info(f"OK: citations.org_code matches count response")
    for c in citations.citations[:2]:
        log.info(
            f"  - title={c.document_title!r} date={c.date!r} "
            f"tags={len(c.topic_tags)} url={c.source_url[:40]!r} "
            f"s3_url={(c.s3_url or '')[:60]!r}"
        )

    log.info("Smoke test complete.")


if __name__ == "__main__":
    asyncio.run(main())
