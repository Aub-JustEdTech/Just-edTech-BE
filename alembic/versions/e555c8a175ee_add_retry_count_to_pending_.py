"""add_retry_count_to_pending_classifications

Adds ``pending_classifications.retry_count`` (INTEGER NOT NULL DEFAULT 0).
Incremented each time ``poll_batch`` resets a stranded ``submitted`` row back
to ``pending`` after its batch ended failed/expired/cancelled; once it exceeds
``HEATMAP_INGEST_MAX_BATCH_RETRIES`` the row is parked at ``dead_letter``
instead of retrying forever.

Revision ID: e555c8a175ee
Revises: 20260813_000001
Create Date: 2026-08-24 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'e555c8a175ee'
down_revision: Union[str, None] = '20260813_000001'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'pending_classifications',
        sa.Column('retry_count', sa.Integer(), server_default='0', nullable=False),
    )


def downgrade() -> None:
    op.drop_column('pending_classifications', 'retry_count')
