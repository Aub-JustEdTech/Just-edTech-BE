"""
CRUD for stored school URL discovery results.
"""

from __future__ import annotations

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.school import School
from app.models.school_url_discovery import SchoolUrlCandidate, SchoolUrlDiscovery
from app.utils.school_url_candidates import dedupe_and_rank_candidates


async def get_discovery_for_school(
    db: AsyncSession,
    tenant_id: int,
    school_id: int,
    *,
    load_candidates: bool = False,
) -> SchoolUrlDiscovery | None:
    stmt = select(SchoolUrlDiscovery).where(
        SchoolUrlDiscovery.school_id == school_id,
        SchoolUrlDiscovery.tenant_id == tenant_id,
    )
    if load_candidates:
        stmt = stmt.options(selectinload(SchoolUrlDiscovery.candidates))
    return (await db.execute(stmt)).scalar_one_or_none()


async def get_discovery_summaries(
    db: AsyncSession,
    tenant_id: int,
    school_ids: list[int],
) -> dict[int, dict[str, object]]:
    """Return per-school discovery summary for list enrichment."""
    if not school_ids:
        return {}

    discovery_rows = (
        await db.execute(
            select(
                SchoolUrlDiscovery.school_id,
                SchoolUrlDiscovery.error,
                func.count(SchoolUrlCandidate.id).label("candidate_count"),
            )
            .outerjoin(
                SchoolUrlCandidate,
                SchoolUrlCandidate.discovery_id == SchoolUrlDiscovery.id,
            )
            .where(
                SchoolUrlDiscovery.tenant_id == tenant_id,
                SchoolUrlDiscovery.school_id.in_(school_ids),
            )
            .group_by(SchoolUrlDiscovery.school_id, SchoolUrlDiscovery.error)
        )
    ).all()

    summaries: dict[int, dict[str, object]] = {}
    for school_id, error, candidate_count in discovery_rows:
        summaries[school_id] = {
            "error": error,
            "candidate_count": int(candidate_count or 0),
        }
    return summaries


async def list_candidates_for_school(
    db: AsyncSession,
    tenant_id: int,
    school_id: int,
    *,
    max_candidates: int,
) -> tuple[SchoolUrlDiscovery | None, list[SchoolUrlCandidate]]:
    discovery = await get_discovery_for_school(
        db, tenant_id, school_id, load_candidates=True
    )
    if not discovery:
        return None, []

    ranked = sorted(discovery.candidates, key=lambda row: row.rank)
    return discovery, ranked[:max_candidates]


async def replace_discovery_for_school(
    db: AsyncSession,
    school: School,
    *,
    discovery_method: str | None,
    total_urls_scanned: int,
    error: str | None,
    raw_candidates: list[dict],
    max_candidates: int,
) -> SchoolUrlDiscovery:
    """
    Upsert discovery metadata and replace all stored candidates for a school.
    """
    deduped = dedupe_and_rank_candidates(
        raw_candidates,
        max_candidates=max_candidates,
    )

    discovery = await get_discovery_for_school(
        db, school.tenant_id, school.id, load_candidates=True
    )
    if discovery is None:
        discovery = SchoolUrlDiscovery(
            school_id=school.id,
            tenant_id=school.tenant_id,
        )
        db.add(discovery)
        await db.flush()
    else:
        await db.execute(
            delete(SchoolUrlCandidate).where(
                SchoolUrlCandidate.discovery_id == discovery.id
            )
        )

    discovery.discovery_method = discovery_method
    discovery.total_urls_scanned = total_urls_scanned
    discovery.error = error

    for index, candidate in enumerate(deduped, start=1):
        db.add(
            SchoolUrlCandidate(
                school_id=school.id,
                tenant_id=school.tenant_id,
                discovery_id=discovery.id,
                url=candidate["url"],
                url_hash=candidate["url_hash"],
                matched_keywords=candidate["matched_keywords"],
                score=candidate["score"],
                rank=index,
            )
        )

    await db.commit()
    await db.refresh(discovery)
    return discovery
