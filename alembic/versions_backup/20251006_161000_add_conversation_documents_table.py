"""add_conversation_documents_and_feedback_tables

Revision ID: 20251006_161000
Revises: 20251006_160001
Create Date: 2025-10-06 16:10:00.000000

This migration creates the conversation_documents association table and feedback table
AFTER the bootstrap migration that creates conversations and documents tables.
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "20251006_161000"
down_revision: str | None = "20251006_160001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Create tables that depend on conversations and documents
    # This must be done AFTER conversations and documents tables exist
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = set(inspector.get_table_names())
    
    # Create conversation_documents association table
    if "conversation_documents" not in existing_tables:
        op.create_table(
            "conversation_documents",
            sa.Column("conversation_id", sa.BigInteger(), nullable=False),
            sa.Column("document_id", sa.BigInteger(), nullable=False),
            sa.ForeignKeyConstraint(
                ["conversation_id"], ["conversations.id"], ondelete="CASCADE"
            ),
            sa.ForeignKeyConstraint(["document_id"], ["documents.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("conversation_id", "document_id"),
            sa.UniqueConstraint(
                "conversation_id", "document_id", name="uq_conversation_document"
            ),
        )
    
    # Create feedback table
    if "feedback" not in existing_tables:
        op.create_table(
            "feedback",
            sa.Column("id", sa.BigInteger(), nullable=False),
            sa.Column("c_id", sa.BigInteger(), nullable=False),
            sa.Column("messages", sa.Text(), nullable=True),
            sa.Column("feedback", sa.Text(), nullable=True),
            sa.Column("is_positive", sa.Boolean(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["c_id"], ["conversations.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index(op.f("ix_feedback_id"), "feedback", ["id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_feedback_id"), table_name="feedback")
    op.drop_table("feedback")
    op.drop_table("conversation_documents")

