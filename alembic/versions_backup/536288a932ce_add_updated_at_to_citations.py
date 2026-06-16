"""add_updated_at_to_citations

Revision ID: 536288a932ce
Revises: 20251006_174701
Create Date: 2025-10-07 13:06:44.954638

"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "536288a932ce"
down_revision: str | None = "20251006_174701"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Add updated_at column to citations table
    op.add_column(
        "citations",
        sa.Column(
            "updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()
        ),
    )


def downgrade() -> None:
    # Remove updated_at column from citations table
    op.drop_column("citations", "updated_at")
