"""add_heatmap_tables

Revision ID: 20260618_000001
Revises: 7336236c2751
Create Date: 2026-06-18 00:00:01

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260618_000001"
down_revision: Union[str, None] = "20260311_000001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "county_district_mapping",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("district_name", sa.String(), nullable=False),
        sa.Column("county_name", sa.String(), nullable=False),
        sa.Column("state", sa.String(2), nullable=False, server_default="MA"),
        sa.Column("fips_code", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_county_district_mapping_id", "county_district_mapping", ["id"], unique=False
    )
    op.create_index(
        "ix_county_district_mapping_district_state",
        "county_district_mapping",
        ["district_name", "state"],
        unique=False,
    )

    op.create_table(
        "heatmap_keywords",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("tenant_id", sa.BigInteger(), nullable=False),
        sa.Column("label", sa.String(), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_heatmap_keywords_id", "heatmap_keywords", ["id"], unique=False)
    op.create_index(
        "ix_heatmap_keywords_tenant_id", "heatmap_keywords", ["tenant_id"], unique=False
    )
    op.create_index(
        "ix_heatmap_keywords_tenant_active_order",
        "heatmap_keywords",
        ["tenant_id", "is_active", "sort_order"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_heatmap_keywords_tenant_active_order", table_name="heatmap_keywords")
    op.drop_index("ix_heatmap_keywords_tenant_id", table_name="heatmap_keywords")
    op.drop_index("ix_heatmap_keywords_id", table_name="heatmap_keywords")
    op.drop_table("heatmap_keywords")

    op.drop_index("ix_county_district_mapping_district_state", table_name="county_district_mapping")
    op.drop_index("ix_county_district_mapping_id", table_name="county_district_mapping")
    op.drop_table("county_district_mapping")
