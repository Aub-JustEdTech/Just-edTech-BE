"""Populate name, title, welcome_message, bot_avatar, is_default in config_version_history

This migration ensures that name, title, welcome_message, bot_avatar, and is_default
are stored in config_version_history for all existing chatbot configs.

Revision ID: 20251113_184044
Revises: 20251112_fix_migration_issues
Create Date: 2025-11-13 18:40:44

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = '20251113_184044'
down_revision = '20251112_fix_migration_issues'
branch_labels = None
depends_on = None


def upgrade():
    """
    Populate config_version_history with name, title, welcome_message, bot_avatar, is_default
    for all existing chatbot configs.
    """
    
    # Step 1: For records with NULL or empty config_version_history,
    # create version 0 with all fields from columns
    op.execute("""
        UPDATE chatbot_configs
        SET config_version_history = jsonb_build_array(
            jsonb_build_object(
                'version', 0,
                'timestamp', COALESCE(created_at, NOW()),
                'config', jsonb_build_object(
                    'id', id,
                    'name', name,
                    'title', title,
                    'welcome_message', welcome_message,
                    'bot_avatar', bot_avatar,
                    'is_default', COALESCE(is_default, false)
                )
            )
        )
        WHERE config_version_history IS NULL 
           OR config_version_history = '[]'::jsonb
           OR jsonb_array_length(config_version_history) = 0
    """)
    
    # Step 2: For records with existing config_version_history,
    # update the latest version to include missing fields
    # Use a PL/pgSQL block for better control
    op.execute("""
        DO $$
        DECLARE
            rec RECORD;
            latest_version_index INTEGER;
            latest_config JSONB;
            updated_config JSONB;
            updated_history JSONB;
        BEGIN
            FOR rec IN 
                SELECT id, name, title, welcome_message, bot_avatar, is_default, config_version_history
                FROM chatbot_configs
                WHERE config_version_history IS NOT NULL
                  AND jsonb_array_length(config_version_history) > 0
            LOOP
                latest_version_index := jsonb_array_length(rec.config_version_history) - 1;
                latest_config := rec.config_version_history->latest_version_index->'config';
                
                -- Merge column values into config, preserving existing values if present
                updated_config := COALESCE(latest_config, '{}'::jsonb) || jsonb_build_object(
                    'id', rec.id,
                    'name', COALESCE(latest_config->>'name', rec.name),
                    'title', COALESCE(latest_config->>'title', rec.title),
                    'welcome_message', COALESCE(latest_config->>'welcome_message', rec.welcome_message),
                    'bot_avatar', COALESCE(latest_config->>'bot_avatar', rec.bot_avatar),
                    'is_default', COALESCE(
                        (latest_config->>'is_default')::boolean,
                        rec.is_default
                    )
                );
                
                -- Update the latest version's config
                updated_history := jsonb_set(
                    rec.config_version_history,
                    ARRAY[latest_version_index::text, 'config'],
                    updated_config
                );
                
                -- Update the record
                UPDATE chatbot_configs
                SET config_version_history = updated_history
                WHERE id = rec.id;
            END LOOP;
        END $$;
    """)


def downgrade():
    """
    Downgrade removes name, title, welcome_message, bot_avatar, is_default from config_version_history.
    However, this is not recommended as it would lose data.
    The columns still exist, so data is preserved there.
    """
    # Note: We don't remove these fields on downgrade to preserve data
    # The columns still exist, so the data is safe
    pass

