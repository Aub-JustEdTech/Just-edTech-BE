"""Create required PostgreSQL extensions.

Previously handled by init.sql (docker entrypoint); now done here so that
alembic upgrade head is self-sufficient against any fresh database, whether
it is a local host-installed Postgres or AWS RDS.

Revision ID: 20251103_000000
Revises:
Create Date: 2025-11-03 00:00:00.000000
"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "20251103_000000"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute('CREATE EXTENSION IF NOT EXISTS "uuid-ossp"')
    op.execute('CREATE EXTENSION IF NOT EXISTS "pg_trgm"')
    op.execute('CREATE EXTENSION IF NOT EXISTS "btree_gin"')


def downgrade() -> None:
    # Extensions are shared database-level objects; dropping them could affect
    # other schemas or future migrations, so downgrade is intentionally a no-op.
    pass
