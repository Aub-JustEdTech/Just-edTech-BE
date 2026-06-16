"""merge migration heads

Revision ID: 45663db3b64d
Revises: 04d537f99d07, 536288a932ce
Create Date: 2025-10-07 19:29:13.985723

"""
from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = "45663db3b64d"
down_revision: str | None = ("04d537f99d07", "536288a932ce")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
