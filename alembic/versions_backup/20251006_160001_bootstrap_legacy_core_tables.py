"""bootstrap legacy core tables

Revision ID: 20251006_160001
Revises:
Create Date: 2025-10-06 16:00:01

This migration bootstraps the legacy base tables expected by later migrations
so a fresh database can apply the migration chain without missing-table errors.
It creates minimal legacy versions of users, conversations, documents, messages.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "20251006_160001"
down_revision: str | None = "0ccc8288ae0a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    existing_tables = set(inspector.get_table_names())

    def table_exists(name: str) -> bool:
        return name in existing_tables

    def index_exists(table: str, index_name: str) -> bool:
        try:
            for ix in inspector.get_indexes(table):
                if ix.get("name") == index_name:
                    return True
        except Exception:
            return False
        return False

    # users (legacy shape: username, full_name, hashed_password, flags)
    if not table_exists("users"):
        op.create_table(
            "users",
            sa.Column(
                "id", sa.INTEGER(), primary_key=True, autoincrement=True, nullable=False
            ),
            sa.Column("full_name", sa.VARCHAR(), nullable=True),
            sa.Column("username", sa.VARCHAR(), nullable=False),
            sa.Column("email", sa.VARCHAR(), nullable=True),
            sa.Column("is_active", sa.BOOLEAN(), nullable=True),
            sa.Column("hashed_password", sa.VARCHAR(), nullable=False),
            sa.Column("is_superuser", sa.BOOLEAN(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
        )
    if table_exists("users") and not index_exists("users", op.f("ix_users_username")):
        op.create_index(op.f("ix_users_username"), "users", ["username"], unique=True)
    if table_exists("users") and not index_exists("users", op.f("ix_users_email")):
        op.create_index(op.f("ix_users_email"), "users", ["email"], unique=True)

    # conversations (legacy shape: title, description, user_id)
    if not table_exists("conversations"):
        op.create_table(
            "conversations",
            sa.Column(
                "id", sa.INTEGER(), primary_key=True, autoincrement=True, nullable=False
            ),
            sa.Column(
                "user_id", sa.INTEGER(), sa.ForeignKey("users.id"), nullable=False
            ),
            sa.Column("title", sa.VARCHAR(), nullable=False),
            sa.Column("description", sa.TEXT(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
        )
    if table_exists("conversations") and not index_exists(
        "conversations", op.f("ix_conversations_id")
    ):
        op.create_index(
            op.f("ix_conversations_id"), "conversations", ["id"], unique=False
        )

    # documents (legacy shape that later gets reshaped heavily)
    if not table_exists("documents"):
        op.create_table(
            "documents",
            sa.Column(
                "id", sa.INTEGER(), primary_key=True, autoincrement=True, nullable=False
            ),
            sa.Column(
                "owner_id", sa.INTEGER(), sa.ForeignKey("users.id"), nullable=False
            ),
            sa.Column("title", sa.VARCHAR(), nullable=False),
            sa.Column("content", sa.TEXT(), nullable=False),
            sa.Column("file_path", sa.VARCHAR(), nullable=True),
            sa.Column("file_type", sa.VARCHAR(), nullable=True),
            sa.Column("file_size", sa.INTEGER(), nullable=True),
            sa.Column("processing_status", sa.VARCHAR(), nullable=True),
            sa.Column(
                "doc_metadata", postgresql.JSON(astext_type=sa.Text()), nullable=True
            ),
            sa.Column("embedding_model", sa.VARCHAR(), nullable=True),
            sa.Column("chunk_count", sa.INTEGER(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
        )
    if table_exists("documents") and not index_exists(
        "documents", op.f("ix_documents_id")
    ):
        op.create_index(op.f("ix_documents_id"), "documents", ["id"], unique=False)

    # messages (legacy, to be dropped/reshaped later)
    if not table_exists("messages"):
        op.create_table(
            "messages",
            sa.Column(
                "id", sa.INTEGER(), primary_key=True, autoincrement=True, nullable=False
            ),
            sa.Column(
                "conversation_id",
                sa.INTEGER(),
                sa.ForeignKey("conversations.id"),
                nullable=False,
            ),
            sa.Column("role", sa.VARCHAR(), nullable=False),
            sa.Column("content", sa.TEXT(), nullable=False),
            sa.Column("context_documents", sa.TEXT(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
        )
    if table_exists("messages") and not index_exists(
        "messages", op.f("ix_messages_id")
    ):
        op.create_index(op.f("ix_messages_id"), "messages", ["id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_messages_id"), table_name="messages")
    op.drop_table("messages")
    op.drop_index(op.f("ix_documents_id"), table_name="documents")
    op.drop_table("documents")
    op.drop_index(op.f("ix_conversations_id"), table_name="conversations")
    op.drop_table("conversations")
    op.drop_index(op.f("ix_users_email"), table_name="users")
    op.drop_index(op.f("ix_users_username"), table_name="users")
    op.drop_table("users")
