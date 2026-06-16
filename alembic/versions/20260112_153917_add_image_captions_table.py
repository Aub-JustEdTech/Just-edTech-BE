"""add_image_captions_table

Revision ID: 20260112_153917
Revises: 7336236c2751
Create Date: 2026-01-12 15:39:17

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '20260112_153917'
down_revision: Union[str, None] = '7336236c2751'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Create image_captions table
    op.create_table(
        'image_captions',
        sa.Column('id', sa.BigInteger(), nullable=False),
        sa.Column('document_id', sa.BigInteger(), nullable=False),
        sa.Column('tenant_id', sa.BigInteger(), nullable=False),
        sa.Column('image_file_path', sa.String(), nullable=False),
        sa.Column('image_url', sa.String(), nullable=True),
        sa.Column('page_number', sa.Integer(), nullable=False),
        sa.Column('image_index', sa.Integer(), nullable=False),
        sa.Column('caption', sa.Text(), nullable=False),
        sa.Column('surrounding_text_before', sa.Text(), nullable=True),
        sa.Column('surrounding_text_after', sa.Text(), nullable=True),
        sa.Column('width', sa.Integer(), nullable=True),
        sa.Column('height', sa.Integer(), nullable=True),
        sa.Column('size_bytes', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['document_id'], ['documents.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_image_captions_document_id'), 'image_captions', ['document_id'], unique=False)
    op.create_index(op.f('ix_image_captions_tenant_id'), 'image_captions', ['tenant_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_image_captions_tenant_id'), table_name='image_captions')
    op.drop_index(op.f('ix_image_captions_document_id'), table_name='image_captions')
    op.drop_table('image_captions')
