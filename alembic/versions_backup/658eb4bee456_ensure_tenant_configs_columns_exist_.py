"""ensure tenant_configs columns exist idempotently

Revision ID: 658eb4bee456
Revises: ad8830554fbb
Create Date: 2025-10-13 14:26:00.291532

"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "658eb4bee456"
down_revision: str | None = "ad8830554fbb"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    conn = op.get_bind()

    # Ensure tenant_configs exists before altering (fresh DBs should have it)
    # Add chunk_size, chunk_overlap, vector_store_type if missing
    conn.execute(
        sa.text(
            """
            DO $$ BEGIN
                IF EXISTS (
                    SELECT 1 FROM information_schema.tables
                    WHERE table_name = 'tenant_configs'
                ) THEN
                    ALTER TABLE tenant_configs
                        ADD COLUMN IF NOT EXISTS chunk_size BIGINT NOT NULL DEFAULT 1000,
                        ADD COLUMN IF NOT EXISTS chunk_overlap BIGINT NOT NULL DEFAULT 200,
                        ADD COLUMN IF NOT EXISTS vector_store_type VARCHAR NOT NULL DEFAULT 'chroma';
                END IF;
            END $$;
            """
        )
    )


def downgrade() -> None:
    # No-op: reconciliation migration
    pass
