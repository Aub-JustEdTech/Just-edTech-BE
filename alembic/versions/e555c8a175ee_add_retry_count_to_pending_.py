"""add_retry_count_to_pending_classifications

Revision ID: e555c8a175ee
Revises: 20260729_000001
Create Date: 2026-08-11 13:14:43.179146

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'e555c8a175ee'
down_revision: Union[str, None] = '20260729_000001'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'pending_classifications',
        sa.Column('retry_count', sa.Integer(), server_default='0', nullable=False),
    )


def downgrade() -> None:
    op.drop_column('pending_classifications', 'retry_count')

