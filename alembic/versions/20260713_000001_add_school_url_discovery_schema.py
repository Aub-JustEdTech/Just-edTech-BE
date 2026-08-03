"""add_school_url_discovery_schema

Stores pre-computed meeting-archive URL discovery results per school district.

Revision ID: 20260713_000001
Revises: 20260710_000001
Create Date: 2026-07-13 00:00:01
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260713_000001"
down_revision: Union[str, None] = "20260710_000001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "school_url_discoveries",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("school_id", sa.BigInteger(), nullable=False),
        sa.Column("tenant_id", sa.BigInteger(), nullable=False),
        sa.Column("discovery_method", sa.String(length=32), nullable=True),
        sa.Column("total_urls_scanned", sa.Integer(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["school_id"], ["schools.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("school_id", name="uq_school_url_discovery_school"),
    )
    op.create_index(
        op.f("ix_school_url_discoveries_id"),
        "school_url_discoveries",
        ["id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_school_url_discoveries_school_id"),
        "school_url_discoveries",
        ["school_id"],
        unique=False,
    )
    op.create_index(
        "ix_school_url_discoveries_tenant_id",
        "school_url_discoveries",
        ["tenant_id"],
        unique=False,
    )

    op.create_table(
        "school_url_candidates",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("school_id", sa.BigInteger(), nullable=False),
        sa.Column("tenant_id", sa.BigInteger(), nullable=False),
        sa.Column("discovery_id", sa.BigInteger(), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("url_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "matched_keywords",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("score", sa.SmallInteger(), nullable=False),
        sa.Column("rank", sa.SmallInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["discovery_id"], ["school_url_discoveries.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["school_id"], ["schools.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "school_id",
            "url_hash",
            name="uq_school_url_candidate_school_hash",
        ),
    )
    op.create_index(
        op.f("ix_school_url_candidates_id"),
        "school_url_candidates",
        ["id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_school_url_candidates_school_id"),
        "school_url_candidates",
        ["school_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_school_url_candidates_discovery_id"),
        "school_url_candidates",
        ["discovery_id"],
        unique=False,
    )
    op.create_index(
        "ix_school_url_candidates_school_rank",
        "school_url_candidates",
        ["school_id", "rank"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_school_url_candidates_school_rank", table_name="school_url_candidates"
    )
    op.drop_index(
        op.f("ix_school_url_candidates_discovery_id"),
        table_name="school_url_candidates",
    )
    op.drop_index(
        op.f("ix_school_url_candidates_school_id"), table_name="school_url_candidates"
    )
    op.drop_index(op.f("ix_school_url_candidates_id"), table_name="school_url_candidates")
    op.drop_table("school_url_candidates")

    op.drop_index(
        "ix_school_url_discoveries_tenant_id", table_name="school_url_discoveries"
    )
    op.drop_index(
        op.f("ix_school_url_discoveries_school_id"),
        table_name="school_url_discoveries",
    )
    op.drop_index(
        op.f("ix_school_url_discoveries_id"), table_name="school_url_discoveries"
    )
    op.drop_table("school_url_discoveries")
