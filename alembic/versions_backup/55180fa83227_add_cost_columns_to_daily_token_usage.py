"""add_cost_columns_to_daily_token_usage

Revision ID: 55180fa83227
Revises: 59a533e48d22
Create Date: 2025-10-17 21:07:05.483826

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '55180fa83227'
down_revision: Union[str, None] = '59a533e48d22'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add columns only if they don't exist
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    
    # Check if daily_token_usage table exists
    if 'daily_token_usage' not in inspector.get_table_names():
        return
    
    columns = [col['name'] for col in inspector.get_columns('daily_token_usage')]
    
    # Add cache tokens column
    if 'total_cache_tokens' not in columns:
        op.add_column('daily_token_usage', sa.Column('total_cache_tokens', sa.BigInteger(), nullable=False, server_default='0'))
    
    # Add cost calculation columns
    if 'input_token_cost' not in columns:
        op.add_column('daily_token_usage', sa.Column('input_token_cost', sa.DECIMAL(precision=12, scale=6), nullable=True, server_default='0', comment='Cost for input tokens in USD'))
    if 'output_token_cost' not in columns:
        op.add_column('daily_token_usage', sa.Column('output_token_cost', sa.DECIMAL(precision=12, scale=6), nullable=True, server_default='0', comment='Cost for output tokens in USD'))
    if 'cache_token_cost' not in columns:
        op.add_column('daily_token_usage', sa.Column('cache_token_cost', sa.DECIMAL(precision=12, scale=6), nullable=True, server_default='0', comment='Cost for cache tokens in USD'))
    if 'total_cost' not in columns:
        op.add_column('daily_token_usage', sa.Column('total_cost', sa.DECIMAL(precision=12, scale=6), nullable=True, server_default='0', comment='Total cost in USD'))


def downgrade() -> None:
    # Remove cost columns
    op.drop_column('daily_token_usage', 'total_cost')
    op.drop_column('daily_token_usage', 'cache_token_cost')
    op.drop_column('daily_token_usage', 'output_token_cost')
    op.drop_column('daily_token_usage', 'input_token_cost')
    op.drop_column('daily_token_usage', 'total_cache_tokens')

