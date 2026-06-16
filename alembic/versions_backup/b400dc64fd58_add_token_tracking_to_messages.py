"""add_token_tracking_to_messages

Revision ID: b400dc64fd58
Revises: 20251016_add_tenant_logo
Create Date: 2025-10-17 20:38:37.807635

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b400dc64fd58'
down_revision: Union[str, None] = '20251016_add_tenant_logo'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add token tracking columns to messages table
    op.add_column('messages', sa.Column('input_tokens', sa.BigInteger(), nullable=True))
    op.add_column('messages', sa.Column('output_tokens', sa.BigInteger(), nullable=True))
    op.add_column('messages', sa.Column('total_tokens', sa.BigInteger(), nullable=True))
    op.add_column('messages', sa.Column('model_used', sa.String(100), nullable=True))
    
    # Set default values for existing records
    op.execute("UPDATE messages SET input_tokens = 0, output_tokens = 0, total_tokens = 0, model_used = 'unknown' WHERE role = 'assistant'")
    op.execute("UPDATE messages SET input_tokens = NULL, output_tokens = NULL, total_tokens = NULL, model_used = NULL WHERE role != 'assistant'")


def downgrade() -> None:
    # Remove token tracking columns from messages table
    op.drop_column('messages', 'model_used')
    op.drop_column('messages', 'total_tokens')
    op.drop_column('messages', 'output_tokens')
    op.drop_column('messages', 'input_tokens')

