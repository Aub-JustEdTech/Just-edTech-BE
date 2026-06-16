"""Rename tenant_configs to chatbot_configs and add chatbot features

This migration:
- Renames tenant_configs table to chatbot_configs
- Adds new fields: name, title, welcome_message, bot_avatar, is_default
- Adds unique constraint on (tenant_id, name)
- Updates conversations table with chatbot_config_id and chatbot_config_snapshot
- Updates monitoring and performance_metrics tables to use chatbot_config_id
- Migrates existing data to set default chatbot names and flags

Revision ID: 20251110_171948
Revises: 20251104_initial_complete_schema
Create Date: 2025-11-10 17:19:48

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = '20251110_171948'
down_revision = '20251104_initial_complete_schema'
branch_labels = None
depends_on = None


def upgrade():
    # Step 1: Rename tenant_configs table to chatbot_configs
    op.rename_table('tenant_configs', 'chatbot_configs')
    
    # Step 2: Add new columns to chatbot_configs
    op.add_column('chatbot_configs', sa.Column('name', sa.String(), nullable=True))
    op.add_column('chatbot_configs', sa.Column('title', sa.String(), nullable=True))
    op.add_column('chatbot_configs', sa.Column('welcome_message', sa.Text(), nullable=True))
    op.add_column('chatbot_configs', sa.Column('bot_avatar', sa.String(), nullable=True))
    op.add_column('chatbot_configs', sa.Column('is_default', sa.Boolean(), nullable=False, server_default='false'))
    
    # Step 3: Migrate existing data - set default values for existing configs
    # Set name to "Default Bot" and is_default to True for all existing configs
    op.execute("""
        UPDATE chatbot_configs 
        SET name = 'Default Bot',
            is_default = true
        WHERE name IS NULL
    """)
    
    # Step 4: Make name column NOT NULL after setting defaults
    op.alter_column('chatbot_configs', 'name', nullable=False)
    
    # Step 5: Add unique constraint on (tenant_id, name)
    op.create_unique_constraint(
        'uq_chatbot_configs_tenant_name',
        'chatbot_configs',
        ['tenant_id', 'name']
    )
    
    # Step 6: Rename index
    op.drop_index('ix_tenant_configs_id', table_name='chatbot_configs')
    op.create_index('ix_chatbot_configs_id', 'chatbot_configs', ['id'])
    
    # Step 7: Update performance_metrics table
    # Drop the foreign key constraint using raw SQL (handles auto-generated names)
    op.execute("""
        DO $$ 
        DECLARE
            constraint_name TEXT;
        BEGIN
            SELECT tc.constraint_name INTO constraint_name
            FROM information_schema.table_constraints tc
            JOIN information_schema.key_column_usage kcu 
                ON tc.constraint_name = kcu.constraint_name
            WHERE tc.table_name = 'performance_metrics'
            AND tc.constraint_type = 'FOREIGN KEY'
            AND kcu.column_name = 'tenant_config_id';
            
            IF constraint_name IS NOT NULL THEN
                EXECUTE 'ALTER TABLE performance_metrics DROP CONSTRAINT ' || quote_ident(constraint_name);
            END IF;
        END $$;
    """)
    # Rename the column using raw SQL
    op.execute('ALTER TABLE performance_metrics RENAME COLUMN tenant_config_id TO chatbot_config_id')
    # Recreate the foreign key constraint with new name
    op.create_foreign_key(
        'performance_metrics_chatbot_config_id_fkey',
        'performance_metrics',
        'chatbot_configs',
        ['chatbot_config_id'],
        ['id'],
        ondelete='CASCADE'
    )
    
    # Step 8: Update monitoring table
    # Drop the foreign key constraint using raw SQL (handles auto-generated names)
    op.execute("""
        DO $$ 
        DECLARE
            constraint_name TEXT;
        BEGIN
            SELECT tc.constraint_name INTO constraint_name
            FROM information_schema.table_constraints tc
            JOIN information_schema.key_column_usage kcu 
                ON tc.constraint_name = kcu.constraint_name
            WHERE tc.table_name = 'monitoring'
            AND tc.constraint_type = 'FOREIGN KEY'
            AND kcu.column_name = 'tenant_config_id';
            
            IF constraint_name IS NOT NULL THEN
                EXECUTE 'ALTER TABLE monitoring DROP CONSTRAINT ' || quote_ident(constraint_name);
            END IF;
        END $$;
    """)
    # Rename the column using raw SQL
    op.execute('ALTER TABLE monitoring RENAME COLUMN tenant_config_id TO chatbot_config_id')
    # Recreate the foreign key constraint with new name
    op.create_foreign_key(
        'monitoring_chatbot_config_id_fkey',
        'monitoring',
        'chatbot_configs',
        ['chatbot_config_id'],
        ['id'],
        ondelete='SET NULL'
    )
    
    # Step 9: Update conversations table
    # Add chatbot_config_id column (nullable initially for migration)
    op.add_column('conversations', sa.Column('chatbot_config_id', sa.BigInteger(), nullable=True))
    # Add chatbot_config_snapshot column (JSONB)
    op.add_column('conversations', sa.Column('chatbot_config_snapshot', postgresql.JSONB(astext_type=sa.Text()), nullable=True))
    
    # Step 10: Migrate existing conversations to use default chatbot
    # For each tenant, find the default chatbot and assign it to conversations
    op.execute("""
        UPDATE conversations c
        SET chatbot_config_id = (
            SELECT cc.id 
            FROM chatbot_configs cc 
            WHERE cc.tenant_id = c.tenant_id 
            AND cc.is_default = true 
            LIMIT 1
        )
        WHERE c.chatbot_config_id IS NULL
    """)
    
    # Step 11: Make chatbot_config_id NOT NULL after migration
    op.alter_column('conversations', 'chatbot_config_id', nullable=False)
    
    # Step 12: Add foreign key constraint for conversations.chatbot_config_id
    op.create_foreign_key(
        'conversations_chatbot_config_id_fkey',
        'conversations',
        'chatbot_configs',
        ['chatbot_config_id'],
        ['id'],
        ondelete='SET NULL'
    )
    
    # Step 13: Update foreign key constraint names in chatbot_configs
    # Drop old foreign key constraints
    op.drop_constraint('tenant_configs_chat_model_id_fkey', 'chatbot_configs', type_='foreignkey')
    op.drop_constraint('tenant_configs_embedding_model_id_fkey', 'chatbot_configs', type_='foreignkey')
    op.drop_constraint('tenant_configs_vec_db_id_fkey', 'chatbot_configs', type_='foreignkey')
    op.drop_constraint('tenant_configs_tenant_id_fkey', 'chatbot_configs', type_='foreignkey')
    
    # Recreate with new names
    op.create_foreign_key(
        'chatbot_configs_tenant_id_fkey',
        'chatbot_configs',
        'tenants',
        ['tenant_id'],
        ['id'],
        ondelete='CASCADE'
    )
    op.create_foreign_key(
        'chatbot_configs_chat_model_id_fkey',
        'chatbot_configs',
        'llm_models',
        ['chat_model_id'],
        ['id'],
        ondelete='NO ACTION'
    )
    op.create_foreign_key(
        'chatbot_configs_embedding_model_id_fkey',
        'chatbot_configs',
        'llm_models',
        ['embedding_model_id'],
        ['id'],
        ondelete='NO ACTION'
    )
    op.create_foreign_key(
        'chatbot_configs_vec_db_id_fkey',
        'chatbot_configs',
        'llm_models',
        ['vec_db_id'],
        ['id'],
        ondelete='NO ACTION'
    )


def downgrade():
    # Step 1: Drop new foreign key constraints
    op.drop_constraint('conversations_chatbot_config_id_fkey', 'conversations', type_='foreignkey')
    op.drop_constraint('monitoring_chatbot_config_id_fkey', 'monitoring', type_='foreignkey')
    op.drop_constraint('performance_metrics_chatbot_config_id_fkey', 'performance_metrics', type_='foreignkey')
    
    # Step 2: Revert conversations table
    op.drop_column('conversations', 'chatbot_config_snapshot')
    op.drop_column('conversations', 'chatbot_config_id')
    
    # Step 3: Revert monitoring table
    op.execute('ALTER TABLE monitoring RENAME COLUMN chatbot_config_id TO tenant_config_id')
    op.create_foreign_key(
        'monitoring_tenant_config_id_fkey',
        'monitoring',
        'chatbot_configs',
        ['tenant_config_id'],
        ['id'],
        ondelete='SET NULL'
    )
    
    # Step 4: Revert performance_metrics table
    op.execute('ALTER TABLE performance_metrics RENAME COLUMN chatbot_config_id TO tenant_config_id')
    op.create_foreign_key(
        'performance_metrics_tenant_config_id_fkey',
        'performance_metrics',
        'chatbot_configs',
        ['tenant_config_id'],
        ['id'],
        ondelete='CASCADE'
    )
    
    # Step 5: Revert chatbot_configs table
    op.drop_constraint('uq_chatbot_configs_tenant_name', 'chatbot_configs', type_='unique')
    op.drop_index('ix_chatbot_configs_id', table_name='chatbot_configs')
    op.create_index('ix_tenant_configs_id', 'chatbot_configs', ['id'])
    op.drop_column('chatbot_configs', 'is_default')
    op.drop_column('chatbot_configs', 'bot_avatar')
    op.drop_column('chatbot_configs', 'welcome_message')
    op.drop_column('chatbot_configs', 'title')
    op.drop_column('chatbot_configs', 'name')
    
    # Step 6: Revert foreign key constraint names
    op.drop_constraint('chatbot_configs_tenant_id_fkey', 'chatbot_configs', type_='foreignkey')
    op.drop_constraint('chatbot_configs_chat_model_id_fkey', 'chatbot_configs', type_='foreignkey')
    op.drop_constraint('chatbot_configs_embedding_model_id_fkey', 'chatbot_configs', type_='foreignkey')
    op.drop_constraint('chatbot_configs_vec_db_id_fkey', 'chatbot_configs', type_='foreignkey')
    
    op.create_foreign_key(
        'tenant_configs_tenant_id_fkey',
        'chatbot_configs',
        'tenants',
        ['tenant_id'],
        ['id'],
        ondelete='CASCADE'
    )
    op.create_foreign_key(
        'tenant_configs_chat_model_id_fkey',
        'chatbot_configs',
        'llm_models',
        ['chat_model_id'],
        ['id'],
        ondelete='NO ACTION'
    )
    op.create_foreign_key(
        'tenant_configs_embedding_model_id_fkey',
        'chatbot_configs',
        'llm_models',
        ['embedding_model_id'],
        ['id'],
        ondelete='NO ACTION'
    )
    op.create_foreign_key(
        'tenant_configs_vec_db_id_fkey',
        'chatbot_configs',
        'llm_models',
        ['vec_db_id'],
        ['id'],
        ondelete='NO ACTION'
    )
    
    # Step 7: Rename table back
    op.rename_table('chatbot_configs', 'tenant_configs')

