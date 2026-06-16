"""add_pricing_to_llm_models

Revision ID: 59a533e48d22
Revises: 20251017_add_daily_token_usage
Create Date: 2025-10-17 21:05:55.159091

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '59a533e48d22'
down_revision: Union[str, None] = '20251017_add_daily_token_usage'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add pricing columns to llm_models table (only if they don't exist)
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    
    # Check if llm_models table exists
    if 'llm_models' not in inspector.get_table_names():
        return
    
    columns = [col['name'] for col in inspector.get_columns('llm_models')]
    
    if 'input_token_price' not in columns:
        op.add_column('llm_models', sa.Column('input_token_price', sa.DECIMAL(precision=10, scale=6), nullable=True, comment='Price per 1M input tokens'))
    if 'output_token_price' not in columns:
        op.add_column('llm_models', sa.Column('output_token_price', sa.DECIMAL(precision=10, scale=6), nullable=True, comment='Price per 1M output tokens'))
    if 'cache_token_price' not in columns:
        op.add_column('llm_models', sa.Column('cache_token_price', sa.DECIMAL(precision=10, scale=6), nullable=True, comment='Price per 1M cache tokens'))


def downgrade() -> None:
    # Remove pricing columns from llm_models table
    op.drop_column('llm_models', 'cache_token_price')
    op.drop_column('llm_models', 'output_token_price')
    op.drop_column('llm_models', 'input_token_price')

