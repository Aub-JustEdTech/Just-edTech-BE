"""add daily token usage table

Revision ID: 20251017_add_daily_token_usage
Revises: b400dc64fd58
Create Date: 2025-10-17 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '20251017_add_daily_token_usage'
down_revision: Union[str, None] = 'b400dc64fd58'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create daily_token_usage table for storing aggregated token statistics."""
    op.create_table(
        'daily_token_usage',
        sa.Column('id', sa.BigInteger(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.Column('usage_date', sa.Date(), nullable=False),
        sa.Column('tenant_id', sa.BigInteger(), nullable=False),
        sa.Column('model_name', sa.String(length=100), nullable=False),
        sa.Column('total_input_tokens', sa.BigInteger(), nullable=False, server_default='0'),
        sa.Column('total_output_tokens', sa.BigInteger(), nullable=False, server_default='0'),
        sa.Column('total_tokens', sa.BigInteger(), nullable=False, server_default='0'),
        sa.Column('message_count', sa.BigInteger(), nullable=False, server_default='0'),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('tenant_id', 'model_name', 'usage_date', name='uq_tenant_model_date')
    )
    
    # Create indexes for better query performance
    op.create_index('idx_daily_usage_lookup', 'daily_token_usage', ['tenant_id', 'usage_date', 'model_name'])
    op.create_index(op.f('ix_daily_token_usage_usage_date'), 'daily_token_usage', ['usage_date'])
    op.create_index(op.f('ix_daily_token_usage_tenant_id'), 'daily_token_usage', ['tenant_id'])
    op.create_index(op.f('ix_daily_token_usage_model_name'), 'daily_token_usage', ['model_name'])
    op.create_index(op.f('ix_daily_token_usage_id'), 'daily_token_usage', ['id'])


def downgrade() -> None:
    """Drop daily_token_usage table."""
    op.drop_index(op.f('ix_daily_token_usage_id'), table_name='daily_token_usage')
    op.drop_index(op.f('ix_daily_token_usage_model_name'), table_name='daily_token_usage')
    op.drop_index(op.f('ix_daily_token_usage_tenant_id'), table_name='daily_token_usage')
    op.drop_index(op.f('ix_daily_token_usage_usage_date'), table_name='daily_token_usage')
    op.drop_index('idx_daily_usage_lookup', table_name='daily_token_usage')
    op.drop_table('daily_token_usage')

