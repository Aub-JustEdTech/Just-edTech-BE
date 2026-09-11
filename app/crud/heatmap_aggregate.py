"""
CRUD operations for HeatmapAggregate model.

Read helpers grouped by topic + source. The heatmap service reads from
this table for the summary view; the nightly reconciliation task
recomputes it from Qdrant.
"""

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.heatmap_aggregate import HeatmapAggregate


class HeatmapAggregateCRUD:
    """CRUD operations for HeatmapAggregate model."""

    async def get_by_source(
        self, db: AsyncSession, source_id: int
    ) -> list[HeatmapAggregate]:
        """Get all topic aggregates for one school."""
        return list(
            (
                await db.execute(
                    select(HeatmapAggregate).where(
                        HeatmapAggregate.source_id == source_id
                    )
                )
            ).scalars().all()
        )

    async def get_by_source_and_topic(
        self, db: AsyncSession, source_id: int, topic: str
    ) -> HeatmapAggregate | None:
        """Get the aggregate for one (school, topic) pair."""
        return (
            await db.execute(
                select(HeatmapAggregate).where(
                    HeatmapAggregate.source_id == source_id,
                    HeatmapAggregate.topic == topic,
                )
            )
        ).scalar_one_or_none()

    async def get_by_tenant(
        self, db: AsyncSession, tenant_id: int
    ) -> list[HeatmapAggregate]:
        """Get all aggregates for a tenant (joins through schools)."""
        from app.models.school import School

        return list(
            (
                await db.execute(
                    select(HeatmapAggregate)
                    .join(School, School.id == HeatmapAggregate.source_id)
                    .where(School.tenant_id == tenant_id)
                )
            ).scalars().all()
        )

    async def delete_by_source(
        self, db: AsyncSession, source_id: int
    ) -> int:
        """Delete all aggregates for a school (used by reconciliation)."""
        result = await db.execute(
            delete(HeatmapAggregate).where(
                HeatmapAggregate.source_id == source_id
            )
        )
        await db.commit()
        return result.rowcount or 0

    async def upsert(
        self,
        db: AsyncSession,
        *,
        source_id: int,
        topic: str,
        chunk_count: int,
        doc_count: int,
        meeting_count: int,
        last_meeting_date=None,
        action_types: dict | None = None,
    ) -> HeatmapAggregate:
        """Insert or replace one (school, topic) row."""
        existing = await self.get_by_source_and_topic(db, source_id, topic)
        if existing is not None:
            existing.chunk_count = chunk_count
            existing.doc_count = doc_count
            existing.meeting_count = meeting_count
            existing.last_meeting_date = last_meeting_date
            existing.action_types = action_types or {}
            await db.flush()
            return existing
        row = HeatmapAggregate(
            source_id=source_id,
            topic=topic,
            chunk_count=chunk_count,
            doc_count=doc_count,
            meeting_count=meeting_count,
            last_meeting_date=last_meeting_date,
            action_types=action_types or {},
        )
        db.add(row)
        await db.flush()
        return row


heatmap_aggregate_crud = HeatmapAggregateCRUD()
