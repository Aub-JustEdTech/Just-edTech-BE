"""add_page_number_to_citations

Revision ID: 20260311_000001
Revises: 20260306_000002
Create Date: 2026-03-11 00:00:01

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "20260311_000001"
down_revision: Union[str, None] = "20260306_000002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("citations", sa.Column("page_number", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("citations", "page_number")

