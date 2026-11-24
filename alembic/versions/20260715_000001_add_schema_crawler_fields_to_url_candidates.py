"""add_schema_crawler_fields_to_url_candidates

Adds three additive columns to school_url_candidates so the hybrid
schema-driven crawler can persist its per-page classification alongside the
existing keyword-rank fields:
  - data_type            (nullable String; one of DATA_TYPES when populated)
  - is_archive           (non-nullable Boolean, defaults False)
  - data_years_available (non-nullable JSONB, defaults empty list)

All three are additive and default to "no value", so the existing keyword-only
discovery flow is unaffected and SCHOOL_SCRAPER_RANKING_MODE=keyword keeps
working unchanged. `alembic downgrade -1` reverses this cleanly.

Revision ID: 20260715_000001
Revises: 20260713_000001
Create Date: 2026-07-15 00:00:01
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260715_000001"
down_revision: Union[str, None] = "20260713_000001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "school_url_candidates",
        sa.Column("data_type", sa.String(length=32), nullable=True),
    )
    op.add_column(
        "school_url_candidates",
        sa.Column(
            "is_archive",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.add_column(
        "school_url_candidates",
        sa.Column(
            "data_years_available",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )


def downgrade() -> None:
    op.drop_column("school_url_candidates", "data_years_available")
    op.drop_column("school_url_candidates", "is_archive")
    op.drop_column("school_url_candidates", "data_type")
