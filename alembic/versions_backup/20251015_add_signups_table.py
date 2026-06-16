"""add signups table for pre-tenant registrations

Revision ID: 20251015_add_signups
Revises: 20251014_add_tenant_config_perf_knobs
Create Date: 2025-10-15
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "20251015_add_signups"
down_revision: str | None = "b766cabdb1d9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "signups",
        sa.Column("id", sa.BigInteger(), primary_key=True, nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("email", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=True),
        sa.Column("password_hash", sa.String(), nullable=False),
        # tenant fields removed (provided later during setup)
        sa.Column("expires_at", sa.DateTime(), nullable=True),
    )
    op.create_index(op.f("ix_signups_email"), "signups", ["email"], unique=True)
    # no domain index; domain is provided later during setup


def downgrade() -> None:
    op.drop_index(op.f("ix_signups_email"), table_name="signups")
    op.drop_table("signups")
