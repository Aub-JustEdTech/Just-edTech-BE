#!/usr/bin/env python3
"""
Seed heatmap keywords for the Just-EdTech tenant.
Run with: PYTHONPATH=. python scripts/seed_heatmap_keywords.py
"""

import asyncio

from sqlalchemy import select, text

from app.db.connector import get_session
from app.models.heatmap import HeatmapKeyword

KEYWORDS = [
    ("Book Ban", 1),
    ("Budget Cuts", 2),
    ("DEI", 3),
    ("School Safety", 4),
    ("Special Education", 5),
]

TENANT_NAME = "Aubergine Solutions"


async def seed():
    async for session in get_session():
        # Resolve tenant_id by name so this script is not hardcoded to a specific ID
        result = await session.execute(
            text("SELECT id FROM tenants WHERE name = :name LIMIT 1"),
            {"name": TENANT_NAME},
        )
        row = result.fetchone()
        if not row:
            print(f"Tenant '{TENANT_NAME}' not found. Run seed_roles.py first.")
            return

        tenant_id = row[0]
        print(f"Seeding keywords for tenant '{TENANT_NAME}' (id={tenant_id})...")

        existing = await session.execute(
            select(HeatmapKeyword).where(HeatmapKeyword.tenant_id == tenant_id)
        )
        if existing.scalars().all():
            print("Keywords already seeded — skipping.")
            return

        for label, sort_order in KEYWORDS:
            session.add(
                HeatmapKeyword(
                    tenant_id=tenant_id,
                    label=label,
                    sort_order=sort_order,
                    is_active=True,
                )
            )

        await session.commit()
        print(f"✅ {len(KEYWORDS)} keywords seeded successfully.")


if __name__ == "__main__":
    asyncio.run(seed())
