"""merge heatmap-ingest and media-ingest heads

Revision ID: c24950a18e03
Revises: 20260729_000001, 20260731_000001
Create Date: 2026-08-10 14:14:35.262853

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c24950a18e03'
down_revision: Union[str, None] = ('20260729_000001', '20260731_000001')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass

