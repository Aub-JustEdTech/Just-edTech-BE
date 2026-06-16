"""add tenant config performance knobs

Revision ID: 20251014_perf_knobs
Revises: 658eb4bee456_ensure_tenant_configs_columns_exist_
Create Date: 2025-10-14
"""

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "20251014_perf_knobs"
down_revision = "658eb4bee456"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("tenant_configs") as batch_op:
        batch_op.add_column(sa.Column("chat_max_tokens", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("rag_top_k", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("rag_max_history", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("rag_context_chars", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("rag_snippet_chars", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("openai_timeout_s", sa.Integer(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("tenant_configs") as batch_op:
        batch_op.drop_column("openai_timeout_s")
        batch_op.drop_column("rag_snippet_chars")
        batch_op.drop_column("rag_context_chars")
        batch_op.drop_column("rag_max_history")
        batch_op.drop_column("rag_top_k")
        batch_op.drop_column("chat_max_tokens")
