"""add_summarizing_to_processingstage

Revision ID: 20260306_000002
Revises: 20260306_000001
Create Date: 2026-03-06 00:00:02

"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "20260306_000002"
down_revision: Union[str, None] = "20260306_000001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ALTER TYPE ADD VALUE cannot run inside a transaction on PostgreSQL < 12.
    # On PostgreSQL 12+ it is transactional.  The IF NOT EXISTS guard makes
    # this migration idempotent so re-running is safe.
    op.execute("ALTER TYPE processingstage ADD VALUE IF NOT EXISTS 'summarizing'")


def downgrade() -> None:
    # PostgreSQL does not support removing values from an enum type.
    # The only way to reverse this is to recreate the type, which is
    # destructive.  We leave downgrade as a no-op and document the
    # manual procedure here:
    #
    #   1. Create a new enum without 'summarizing'
    #   2. ALTER TABLE ... ALTER COLUMN stage TYPE new_enum USING ...
    #   3. DROP TYPE old_enum; ALTER TYPE new_enum RENAME TO processingstage;
    pass
