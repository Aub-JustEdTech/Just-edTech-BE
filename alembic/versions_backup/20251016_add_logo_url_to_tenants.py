"""add optional logo_url to tenants

Revision ID: 20251016_add_tenant_logo
Revises: 20251015_signup_verify_cols
Create Date: 2025-10-16
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "20251016_add_tenant_logo"
down_revision: str | None = "20251015_signup_verify_cols"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("tenants", sa.Column("logo_url", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("tenants", "logo_url")
