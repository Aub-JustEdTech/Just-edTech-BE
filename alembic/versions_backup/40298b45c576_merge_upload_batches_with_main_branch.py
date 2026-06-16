"""merge upload batches with main branch

Revision ID: 40298b45c576
Revises: 20250108_add_upload_batches, 45663db3b64d
Create Date: 2025-10-09 14:37:25.790475

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '40298b45c576'
down_revision: Union[str, None] = ('20250108_add_upload_batches', '45663db3b64d')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass

