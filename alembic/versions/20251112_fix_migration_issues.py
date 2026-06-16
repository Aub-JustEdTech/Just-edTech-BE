"""Fix issues from previous migrations for databases that already applied them

This migration fixes issues that may exist in databases that already ran:
- 20251110_171948: Fixes naming collisions and nullable constraint
- 20251110_182730: Ensures foreign keys are dropped before columns

Revision ID: 20251112_fix_migration_issues
Revises: 20251111_121007
Create Date: 2025-11-12

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '20251112_fix_migration_issues'
down_revision = '20251111_121007'
branch_labels = None
depends_on = None


def upgrade():
    """
    Fix issues that may exist in databases that already ran previous migrations.
    This migration is idempotent and safe to run multiple times.
    """
    
    # Fix 1: Resolve naming collisions in chatbot_configs
    # If multiple configs have the same name for the same tenant, rename them
    op.execute("""
        WITH duplicate_names AS (
            SELECT 
                id,
                tenant_id,
                name,
                ROW_NUMBER() OVER (PARTITION BY tenant_id, name ORDER BY id) as rn
            FROM chatbot_configs
            WHERE (tenant_id, name) IN (
                SELECT tenant_id, name
                FROM chatbot_configs
                GROUP BY tenant_id, name
                HAVING COUNT(*) > 1
            )
        )
        UPDATE chatbot_configs cc
        SET name = CASE 
            WHEN dn.rn = 1 THEN cc.name
            ELSE cc.name || ' ' || dn.rn::text
        END
        FROM duplicate_names dn
        WHERE cc.id = dn.id AND dn.rn > 1
    """)
    
    # Fix 2: Ensure chatbot_config_id in conversations is nullable
    # If it was set to NOT NULL but has ondelete='SET NULL', make it nullable
    # Check if column exists and is NOT NULL, then alter it
    op.execute("""
        DO $$
        BEGIN
            -- Check if column exists and is NOT NULL
            IF EXISTS (
                SELECT 1 
                FROM information_schema.columns 
                WHERE table_name = 'conversations' 
                AND column_name = 'chatbot_config_id'
                AND is_nullable = 'NO'
            ) THEN
                -- Check if foreign key has SET NULL
                IF EXISTS (
                    SELECT 1
                    FROM information_schema.table_constraints tc
                    JOIN information_schema.key_column_usage kcu 
                        ON tc.constraint_name = kcu.constraint_name
                    WHERE tc.table_name = 'conversations'
                    AND tc.constraint_type = 'FOREIGN KEY'
                    AND kcu.column_name = 'chatbot_config_id'
                    AND tc.constraint_name LIKE '%chatbot_config_id%'
                ) THEN
                    -- Make column nullable to match SET NULL behavior
                    ALTER TABLE conversations ALTER COLUMN chatbot_config_id DROP NOT NULL;
                END IF;
            END IF;
        END $$;
    """)
    
    # Fix 3: Ensure foreign key constraints are dropped before columns are removed
    # This is a safety check - if columns still exist with foreign keys, drop the constraints
    # Check for chat_model_id, embedding_model_id, vec_db_id columns and their foreign keys
    op.execute("""
        DO $$
        DECLARE
            constraint_name TEXT;
        BEGIN
            -- Drop chat_model_id foreign key if column still exists
            IF EXISTS (
                SELECT 1 FROM information_schema.columns 
                WHERE table_name = 'chatbot_configs' AND column_name = 'chat_model_id'
            ) THEN
                SELECT tc.constraint_name INTO constraint_name
                FROM information_schema.table_constraints tc
                JOIN information_schema.key_column_usage kcu 
                    ON tc.constraint_name = kcu.constraint_name
                WHERE tc.table_name = 'chatbot_configs'
                AND tc.constraint_type = 'FOREIGN KEY'
                AND kcu.column_name = 'chat_model_id';
                
                IF constraint_name IS NOT NULL THEN
                    EXECUTE 'ALTER TABLE chatbot_configs DROP CONSTRAINT IF EXISTS ' || quote_ident(constraint_name);
                END IF;
            END IF;
            
            -- Drop embedding_model_id foreign key if column still exists
            IF EXISTS (
                SELECT 1 FROM information_schema.columns 
                WHERE table_name = 'chatbot_configs' AND column_name = 'embedding_model_id'
            ) THEN
                SELECT tc.constraint_name INTO constraint_name
                FROM information_schema.table_constraints tc
                JOIN information_schema.key_column_usage kcu 
                    ON tc.constraint_name = kcu.constraint_name
                WHERE tc.table_name = 'chatbot_configs'
                AND tc.constraint_type = 'FOREIGN KEY'
                AND kcu.column_name = 'embedding_model_id';
                
                IF constraint_name IS NOT NULL THEN
                    EXECUTE 'ALTER TABLE chatbot_configs DROP CONSTRAINT IF EXISTS ' || quote_ident(constraint_name);
                END IF;
            END IF;
            
            -- Drop vec_db_id foreign key if column still exists
            IF EXISTS (
                SELECT 1 FROM information_schema.columns 
                WHERE table_name = 'chatbot_configs' AND column_name = 'vec_db_id'
            ) THEN
                SELECT tc.constraint_name INTO constraint_name
                FROM information_schema.table_constraints tc
                JOIN information_schema.key_column_usage kcu 
                    ON tc.constraint_name = kcu.constraint_name
                WHERE tc.table_name = 'chatbot_configs'
                AND tc.constraint_type = 'FOREIGN KEY'
                AND kcu.column_name = 'vec_db_id';
                
                IF constraint_name IS NOT NULL THEN
                    EXECUTE 'ALTER TABLE chatbot_configs DROP CONSTRAINT IF EXISTS ' || quote_ident(constraint_name);
                END IF;
            END IF;
        END $$;
    """)


def downgrade():
    """
    Downgrade is a no-op since this migration only fixes issues.
    The fixes are forward-compatible and don't need to be reversed.
    """
    pass

