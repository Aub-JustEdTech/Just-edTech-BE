"""add is_verified and verified_at to signups

Revision ID: 20251015_signup_verify_cols
Revises: 20251015_add_signups
Create Date: 2025-10-15
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "20251015_signup_verify_cols"
down_revision: str | None = "20251015_add_signups"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "signups",
        sa.Column(
            "is_verified", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
    )
    op.add_column("signups", sa.Column("verified_at", sa.DateTime(), nullable=True))
    # Drop server_default to keep Python-level default semantics
    op.alter_column(
        "signups",
        "is_verified",
        server_default=None,
        existing_type=sa.Boolean(),
        existing_nullable=False,
    )


def downgrade() -> None:
    op.drop_column("signups", "verified_at")
    op.drop_column("signups", "is_verified")
