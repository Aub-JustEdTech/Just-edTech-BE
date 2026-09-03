"""multi_tenant_auth_overhaul

Revision ID: 20260703_000001
Revises: 20260311_000001
Create Date: 2026-07-03 00:00:01

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260703_000001"
down_revision: Union[str, None] = "20260311_000001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Make users.tenant_id nullable (super admin and tenant admin will have NULL)
    op.alter_column("users", "tenant_id", existing_type=sa.BigInteger(), nullable=True)

    # Make invitations.tenant_id nullable (admin invites have no tenant)
    op.alter_column(
        "invitations", "tenant_id", existing_type=sa.BigInteger(), nullable=True
    )

    # Create user_tenant_access join table for tenant_admin multi-tenant access
    # (table may already exist from a prior development migration — use IF NOT EXISTS)
    op.execute("""
        CREATE TABLE IF NOT EXISTS user_tenant_access (
            id BIGSERIAL PRIMARY KEY,
            user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            tenant_id BIGINT NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            created_at TIMESTAMP DEFAULT now(),
            CONSTRAINT uq_user_tenant_access UNIQUE (user_id, tenant_id)
        )
    """)
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_user_tenant_access_user_id ON user_tenant_access (user_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_user_tenant_access_tenant_id ON user_tenant_access (tenant_id)"
    )


def downgrade() -> None:
    op.drop_index("ix_user_tenant_access_tenant_id", table_name="user_tenant_access")
    op.drop_index("ix_user_tenant_access_user_id", table_name="user_tenant_access")
    op.drop_table("user_tenant_access")
    op.alter_column(
        "invitations", "tenant_id", existing_type=sa.BigInteger(), nullable=False
    )
    op.alter_column("users", "tenant_id", existing_type=sa.BigInteger(), nullable=False)
