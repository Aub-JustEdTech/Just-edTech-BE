"""add_images_to_messages

Revision ID: 1ff7c73a4a52
Revises: 20260112_153917
Create Date: 2026-01-15 13:12:33.450156

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '1ff7c73a4a52'
down_revision: Union[str, None] = '20260112_153917'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add images column to messages table
    op.add_column('messages', sa.Column('images', sa.dialects.postgresql.JSONB(), nullable=True))


def downgrade() -> None:
    # Remove images column from messages table
    op.drop_column('messages', 'images')

