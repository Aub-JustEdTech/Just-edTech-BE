"""add_monthly_billing_table

Revision ID: 7d621db9c58e
Revises: 55180fa83227
Create Date: 2025-10-17 21:08:17.610146

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '7d621db9c58e'
down_revision: Union[str, None] = '55180fa83227'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Check if table already exists
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    
    if 'monthly_billing' in inspector.get_table_names():
        return  # Table already exists, skip creation
    
    # Create monthly_billing table
    op.create_table(
        'monthly_billing',
        sa.Column('id', sa.BigInteger(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.Column('billing_year', sa.Integer(), nullable=False),
        sa.Column('billing_month', sa.Integer(), nullable=False),
        sa.Column('period_start_date', sa.Date(), nullable=False),
        sa.Column('period_end_date', sa.Date(), nullable=False),
        sa.Column('tenant_id', sa.BigInteger(), nullable=False),
        sa.Column('model_name', sa.String(length=100), nullable=False),
        sa.Column('total_input_tokens', sa.BigInteger(), nullable=False, server_default='0'),
        sa.Column('total_output_tokens', sa.BigInteger(), nullable=False, server_default='0'),
        sa.Column('total_tokens', sa.BigInteger(), nullable=False, server_default='0'),
        sa.Column('total_cache_tokens', sa.BigInteger(), nullable=False, server_default='0'),
        sa.Column('message_count', sa.BigInteger(), nullable=False, server_default='0'),
        sa.Column('input_token_cost', sa.DECIMAL(precision=12, scale=6), nullable=False, server_default='0', comment='Cost for input tokens in USD'),
        sa.Column('output_token_cost', sa.DECIMAL(precision=12, scale=6), nullable=False, server_default='0', comment='Cost for output tokens in USD'),
        sa.Column('cache_token_cost', sa.DECIMAL(precision=12, scale=6), nullable=False, server_default='0', comment='Cost for cache tokens in USD'),
        sa.Column('total_cost', sa.DECIMAL(precision=12, scale=6), nullable=False, server_default='0', comment='Total cost for the month in USD'),
        sa.Column('avg_input_token_price', sa.DECIMAL(precision=10, scale=6), nullable=True, comment='Average price per 1M input tokens'),
        sa.Column('avg_output_token_price', sa.DECIMAL(precision=10, scale=6), nullable=True, comment='Average price per 1M output tokens'),
        sa.Column('avg_cache_token_price', sa.DECIMAL(precision=10, scale=6), nullable=True, comment='Average price per 1M cache tokens'),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('tenant_id', 'model_name', 'billing_year', 'billing_month', name='uq_tenant_model_month')
    )
    
    # Create indexes
    op.create_index('idx_monthly_billing_lookup', 'monthly_billing', ['tenant_id', 'billing_year', 'billing_month'])
    op.create_index('idx_monthly_billing_date_range', 'monthly_billing', ['period_start_date', 'period_end_date'])
    op.create_index(op.f('ix_monthly_billing_tenant_id'), 'monthly_billing', ['tenant_id'])
    op.create_index(op.f('ix_monthly_billing_billing_year'), 'monthly_billing', ['billing_year'])
    op.create_index(op.f('ix_monthly_billing_billing_month'), 'monthly_billing', ['billing_month'])
    op.create_index(op.f('ix_monthly_billing_model_name'), 'monthly_billing', ['model_name'])


def downgrade() -> None:
    # Drop indexes
    op.drop_index(op.f('ix_monthly_billing_model_name'), table_name='monthly_billing')
    op.drop_index(op.f('ix_monthly_billing_billing_month'), table_name='monthly_billing')
    op.drop_index(op.f('ix_monthly_billing_billing_year'), table_name='monthly_billing')
    op.drop_index(op.f('ix_monthly_billing_tenant_id'), table_name='monthly_billing')
    op.drop_index('idx_monthly_billing_date_range', table_name='monthly_billing')
    op.drop_index('idx_monthly_billing_lookup', table_name='monthly_billing')
    
    # Drop table
    op.drop_table('monthly_billing')

