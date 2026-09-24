"""add_last_scraped_at_to_school_scrape_urls

Revision ID: ae2cbb605267
Revises: 20260731_000001
Create Date: 2026-08-07 16:31:28.361448

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'ae2cbb605267'
down_revision: Union[str, None] = '20260731_000001'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Autogenerate picked up unrelated drift from this local DB (langgraph
    # checkpoint tables, other domains' index/constraint differences) —
    # trimmed to just this feature's change.
    op.add_column('school_scrape_urls', sa.Column('last_scraped_at', sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column('school_scrape_urls', 'last_scraped_at')

