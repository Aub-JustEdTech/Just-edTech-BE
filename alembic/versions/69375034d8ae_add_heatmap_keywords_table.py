"""add_heatmap_keywords_table

Revision ID: 69375034d8ae
Revises: 20260311_000001
Create Date: 2026-06-29 19:11:29.356677

"""
from typing import Sequence, Union

# revision identifiers, used by Alembic.
revision: str = '69375034d8ae'
down_revision: Union[str, None] = '20260311_000001'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # heatmap_keywords table already existed in the database when this revision
    # was generated — this entry exists solely to keep the migration chain intact.
    pass


def downgrade() -> None:
    pass
