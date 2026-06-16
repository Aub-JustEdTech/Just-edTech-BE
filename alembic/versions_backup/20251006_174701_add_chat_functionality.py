"""add_chat_functionality

Revision ID: 20251006_174701
Revises: aa6f53e5fff9
Create Date: 2025-10-06 17:47:01.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "20251006_174701"
down_revision: str | None = "aa6f53e5fff9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Add title column to conversations table (only if it doesn't exist)
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = [col['name'] for col in inspector.get_columns('conversations')]
    
    if 'title' not in columns:
        op.add_column("conversations", sa.Column("title", sa.String(255), nullable=True))

    # Create messages table (only if it doesn't exist or needs to be recreated)
    existing_tables = set(inspector.get_table_names())
    
    need_to_create_messages = True
    
    # Drop old messages table if it exists (from bootstrap migration)
    if "messages" in existing_tables:
        # Check if this is the old legacy messages table by checking columns
        messages_columns = [col['name'] for col in inspector.get_columns('messages')]
        # Old table has 'context_documents', new one doesn't
        if 'context_documents' in messages_columns:
            op.execute("DROP TABLE IF EXISTS messages CASCADE")
            need_to_create_messages = True
        else:
            # Already has the correct structure
            need_to_create_messages = False
    
    # Now create the new messages table if needed
    if need_to_create_messages:
        op.create_table(
            "messages",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("conversation_id", sa.BigInteger(), nullable=False),
        sa.Column("role", sa.String(50), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["conversation_id"], ["conversations.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        )
        op.create_index(op.f("ix_messages_id"), "messages", ["id"], unique=False)
        op.create_index(
            op.f("ix_messages_conversation_id"),
            "messages",
            ["conversation_id"],
            unique=False,
        )
        op.create_index(
            op.f("ix_messages_created_at"), "messages", ["created_at"], unique=False
        )

    # Create citations table
    op.create_table(
        "citations",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("message_id", sa.BigInteger(), nullable=False),
        sa.Column("document_title", sa.String(500), nullable=True),
        sa.Column("document_url", sa.Text(), nullable=False),
        sa.Column("snippet", sa.Text(), nullable=True),
        sa.Column("position", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["message_id"], ["messages.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_citations_id"), "citations", ["id"], unique=False)
    op.create_index(
        op.f("ix_citations_message_id"), "citations", ["message_id"], unique=False
    )


def downgrade() -> None:
    # Drop citations table
    op.drop_index(op.f("ix_citations_message_id"), table_name="citations")
    op.drop_index(op.f("ix_citations_id"), table_name="citations")
    op.drop_table("citations")

    # Drop messages table
    op.drop_index(op.f("ix_messages_created_at"), table_name="messages")
    op.drop_index(op.f("ix_messages_conversation_id"), table_name="messages")
    op.drop_index(op.f("ix_messages_id"), table_name="messages")
    op.drop_table("messages")

    # Remove title column from conversations
    op.drop_column("conversations", "title")
