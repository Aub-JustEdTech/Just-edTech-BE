"""add_heatmap_keywords_table

Revision ID: 69375034d8ae
Revises: 20260311_000001
Create Date: 2026-06-29 19:11:29.356677

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '69375034d8ae'
down_revision: Union[str, None] = '20260311_000001'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # heatmap_keywords table, uq_conversation_document, and ix_image_captions_id
    # already exist in the database — this revision records the schema state only.
    pass


def downgrade() -> None:
    pass
    op.create_table('checkpoint_blobs',
    sa.Column('thread_id', sa.TEXT(), autoincrement=False, nullable=False),
    sa.Column('checkpoint_ns', sa.TEXT(), server_default=sa.text("''::text"), autoincrement=False, nullable=False),
    sa.Column('channel', sa.TEXT(), autoincrement=False, nullable=False),
    sa.Column('version', sa.TEXT(), autoincrement=False, nullable=False),
    sa.Column('type', sa.TEXT(), autoincrement=False, nullable=False),
    sa.Column('blob', postgresql.BYTEA(), autoincrement=False, nullable=True),
    sa.PrimaryKeyConstraint('thread_id', 'checkpoint_ns', 'channel', 'version', name=op.f('checkpoint_blobs_pkey'))
    )
    op.create_index(op.f('checkpoint_blobs_thread_id_idx'), 'checkpoint_blobs', ['thread_id'], unique=False)
    op.create_table('checkpoint_migrations',
    sa.Column('v', sa.INTEGER(), autoincrement=False, nullable=False),
    sa.PrimaryKeyConstraint('v', name=op.f('checkpoint_migrations_pkey'))
    )
    op.create_table('checkpoints',
    sa.Column('thread_id', sa.TEXT(), autoincrement=False, nullable=False),
    sa.Column('checkpoint_ns', sa.TEXT(), server_default=sa.text("''::text"), autoincrement=False, nullable=False),
    sa.Column('checkpoint_id', sa.TEXT(), autoincrement=False, nullable=False),
    sa.Column('parent_checkpoint_id', sa.TEXT(), autoincrement=False, nullable=True),
    sa.Column('type', sa.TEXT(), autoincrement=False, nullable=True),
    sa.Column('checkpoint', postgresql.JSONB(astext_type=sa.Text()), autoincrement=False, nullable=False),
    sa.Column('metadata', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), autoincrement=False, nullable=False),
    sa.PrimaryKeyConstraint('thread_id', 'checkpoint_ns', 'checkpoint_id', name=op.f('checkpoints_pkey'))
    )
    op.create_index(op.f('checkpoints_thread_id_idx'), 'checkpoints', ['thread_id'], unique=False)
    op.create_table('checkpoint_writes',
    sa.Column('thread_id', sa.TEXT(), autoincrement=False, nullable=False),
    sa.Column('checkpoint_ns', sa.TEXT(), server_default=sa.text("''::text"), autoincrement=False, nullable=False),
    sa.Column('checkpoint_id', sa.TEXT(), autoincrement=False, nullable=False),
    sa.Column('task_id', sa.TEXT(), autoincrement=False, nullable=False),
    sa.Column('idx', sa.INTEGER(), autoincrement=False, nullable=False),
    sa.Column('channel', sa.TEXT(), autoincrement=False, nullable=False),
    sa.Column('type', sa.TEXT(), autoincrement=False, nullable=True),
    sa.Column('blob', postgresql.BYTEA(), autoincrement=False, nullable=False),
    sa.Column('task_path', sa.TEXT(), server_default=sa.text("''::text"), autoincrement=False, nullable=False),
    sa.PrimaryKeyConstraint('thread_id', 'checkpoint_ns', 'checkpoint_id', 'task_id', 'idx', name=op.f('checkpoint_writes_pkey'))
    )
    op.create_index(op.f('checkpoint_writes_thread_id_idx'), 'checkpoint_writes', ['thread_id'], unique=False)
    # ### end Alembic commands ###

