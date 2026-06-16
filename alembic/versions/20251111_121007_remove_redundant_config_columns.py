"""Remove redundant config columns from chatbot_configs

This migration removes all config columns that are now stored in config_version_history JSONB.
These columns are redundant since all config is stored in the version history.

Columns to remove:
- system_prompt
- chat_model_id
- embedding_model_id
- vec_db_id
- search_type
- threshold_value
- temperature
- chat_max_tokens
- rag_top_k
- rag_max_history
- rag_context_chars
- rag_snippet_chars
- openai_timeout_s
- chunk_size
- chunk_overlap
- vector_store_type

Note: tenant_id is kept as it's a foreign key for ownership, not a config field.

Revision ID: 20251111_121007
Revises: 20251110_182730
Create Date: 2025-11-11 12:10:07

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = '20251111_121007'
down_revision = '20251110_182730'
branch_labels = None
depends_on = None


def upgrade():
    # List of columns to remove (all config columns that are in JSON)
    columns_to_remove = [
        'system_prompt',
        'chat_model_id',
        'embedding_model_id',
        'vec_db_id',
        'search_type',
        'threshold_value',
        'temperature',
        'chat_max_tokens',
        'rag_top_k',
        'rag_max_history',
        'rag_context_chars',
        'rag_snippet_chars',
        'openai_timeout_s',
        'chunk_size',
        'chunk_overlap',
        'vector_store_type',
    ]
    
    # Drop each column if it exists (idempotent)
    for column_name in columns_to_remove:
        # Check if column exists before dropping
        op.execute(f"""
            DO $$ 
            BEGIN
                IF EXISTS (
                    SELECT 1 
                    FROM information_schema.columns 
                    WHERE table_name = 'chatbot_configs' 
                    AND column_name = '{column_name}'
                ) THEN
                    ALTER TABLE chatbot_configs DROP COLUMN {column_name};
                END IF;
            END $$;
        """)


def downgrade():
    # Re-add all the columns that were removed
    # Note: We'll use nullable=True since we can't restore the exact values
    op.add_column('chatbot_configs', sa.Column('vector_store_type', sa.String(), nullable=True, server_default='chroma'))
    op.add_column('chatbot_configs', sa.Column('chunk_overlap', sa.BigInteger(), nullable=True, server_default='200'))
    op.add_column('chatbot_configs', sa.Column('chunk_size', sa.BigInteger(), nullable=True, server_default='1000'))
    op.add_column('chatbot_configs', sa.Column('openai_timeout_s', sa.Integer(), nullable=True))
    op.add_column('chatbot_configs', sa.Column('rag_snippet_chars', sa.Integer(), nullable=True))
    op.add_column('chatbot_configs', sa.Column('rag_context_chars', sa.Integer(), nullable=True))
    op.add_column('chatbot_configs', sa.Column('rag_max_history', sa.Integer(), nullable=True))
    op.add_column('chatbot_configs', sa.Column('rag_top_k', sa.Integer(), nullable=True))
    op.add_column('chatbot_configs', sa.Column('chat_max_tokens', sa.Integer(), nullable=True))
    op.add_column('chatbot_configs', sa.Column('temperature', sa.Float(), nullable=True))
    op.add_column('chatbot_configs', sa.Column('threshold_value', sa.Float(), nullable=True))
    op.add_column('chatbot_configs', sa.Column('search_type', sa.String(), nullable=True))
    op.add_column('chatbot_configs', sa.Column('vec_db_id', sa.BigInteger(), sa.ForeignKey('llm_models.id'), nullable=True))
    op.add_column('chatbot_configs', sa.Column('embedding_model_id', sa.BigInteger(), sa.ForeignKey('llm_models.id'), nullable=True))
    op.add_column('chatbot_configs', sa.Column('chat_model_id', sa.BigInteger(), sa.ForeignKey('llm_models.id'), nullable=True))
    op.add_column('chatbot_configs', sa.Column('system_prompt', sa.Text(), nullable=True))
    
    # Try to populate columns from latest version in history (if available)
    op.execute("""
        UPDATE chatbot_configs
        SET 
            system_prompt = (config_version_history->-1->'config'->>'system_prompt')::text,
            chat_model_id = CASE 
                WHEN (config_version_history->-1->'config'->>'chat_model_id')::text != 'null' 
                THEN ((config_version_history->-1->'config'->>'chat_model_id')::bigint)
                ELSE NULL 
            END,
            embedding_model_id = CASE 
                WHEN (config_version_history->-1->'config'->>'embedding_model_id')::text != 'null' 
                THEN ((config_version_history->-1->'config'->>'embedding_model_id')::bigint)
                ELSE NULL 
            END,
            vec_db_id = CASE 
                WHEN (config_version_history->-1->'config'->>'vec_db_id')::text != 'null' 
                THEN ((config_version_history->-1->'config'->>'vec_db_id')::bigint)
                ELSE NULL 
            END,
            search_type = (config_version_history->-1->'config'->>'search_type'),
            threshold_value = CASE 
                WHEN (config_version_history->-1->'config'->>'threshold_value')::text != 'null' 
                THEN ((config_version_history->-1->'config'->>'threshold_value')::float)
                ELSE NULL 
            END,
            temperature = CASE 
                WHEN (config_version_history->-1->'config'->>'temperature')::text != 'null' 
                THEN ((config_version_history->-1->'config'->>'temperature')::float)
                ELSE NULL 
            END,
            chat_max_tokens = CASE 
                WHEN (config_version_history->-1->'config'->>'chat_max_tokens')::text != 'null' 
                THEN ((config_version_history->-1->'config'->>'chat_max_tokens')::integer)
                ELSE NULL 
            END,
            rag_top_k = CASE 
                WHEN (config_version_history->-1->'config'->>'rag_top_k')::text != 'null' 
                THEN ((config_version_history->-1->'config'->>'rag_top_k')::integer)
                ELSE NULL 
            END,
            rag_max_history = CASE 
                WHEN (config_version_history->-1->'config'->>'rag_max_history')::text != 'null' 
                THEN ((config_version_history->-1->'config'->>'rag_max_history')::integer)
                ELSE NULL 
            END,
            rag_context_chars = CASE 
                WHEN (config_version_history->-1->'config'->>'rag_context_chars')::text != 'null' 
                THEN ((config_version_history->-1->'config'->>'rag_context_chars')::integer)
                ELSE NULL 
            END,
            rag_snippet_chars = CASE 
                WHEN (config_version_history->-1->'config'->>'rag_snippet_chars')::text != 'null' 
                THEN ((config_version_history->-1->'config'->>'rag_snippet_chars')::integer)
                ELSE NULL 
            END,
            openai_timeout_s = CASE 
                WHEN (config_version_history->-1->'config'->>'openai_timeout_s')::text != 'null' 
                THEN ((config_version_history->-1->'config'->>'openai_timeout_s')::integer)
                ELSE NULL 
            END,
            chunk_size = CASE 
                WHEN (config_version_history->-1->'config'->>'chunk_size')::text != 'null' 
                THEN ((config_version_history->-1->'config'->>'chunk_size')::bigint)
                ELSE 1000 
            END,
            chunk_overlap = CASE 
                WHEN (config_version_history->-1->'config'->>'chunk_overlap')::text != 'null' 
                THEN ((config_version_history->-1->'config'->>'chunk_overlap')::bigint)
                ELSE 200 
            END,
            vector_store_type = COALESCE(
                (config_version_history->-1->'config'->>'vector_store_type'),
                'chroma'
            )
        WHERE config_version_history IS NOT NULL 
        AND jsonb_array_length(config_version_history) > 0
    """)

