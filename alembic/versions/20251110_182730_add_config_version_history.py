"""Add config version history to chatbot_configs and version_index to conversations

This migration:
- Adds config_version_history (JSONB) column to chatbot_configs table
- Adds chatbot_config_version_index (Integer) column to conversations table
- Removes chatbot_config_snapshot column from conversations table
- Migrates existing data: creates initial version 0 for all chatbots, sets version_index = 0 for existing conversations
- Removes redundant config columns (all config now stored only in JSON version history)

Revision ID: 20251110_182730
Revises: 20251110_171948
Create Date: 2025-11-10 18:27:30

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = '20251110_182730'
down_revision = '20251110_171948'
branch_labels = None
depends_on = None


def upgrade():
    # Step 1: Add config_version_history to chatbot_configs
    op.add_column(
        'chatbot_configs',
        sa.Column('config_version_history', postgresql.JSONB(astext_type=sa.Text()), nullable=True)
    )
    
    # Step 2: Migrate existing chatbot configs to create initial version 0
    # Create version 0 from current config state for all chatbots
    op.execute("""
        UPDATE chatbot_configs
        SET config_version_history = jsonb_build_array(
            jsonb_build_object(
                'version', 0,
                'timestamp', created_at,
                'config', jsonb_build_object(
                    'id', id,
                    'name', name,
                    'title', title,
                    'system_prompt', system_prompt,
                    'chat_model_id', chat_model_id,
                    'embedding_model_id', embedding_model_id,
                    'vec_db_id', vec_db_id,
                    'temperature', temperature,
                    'chat_max_tokens', chat_max_tokens,
                    'rag_top_k', rag_top_k,
                    'rag_max_history', rag_max_history,
                    'threshold_value', threshold_value,
                    'chunk_size', chunk_size,
                    'chunk_overlap', chunk_overlap,
                    'vector_store_type', vector_store_type,
                    'search_type', search_type,
                    'rag_context_chars', rag_context_chars,
                    'rag_snippet_chars', rag_snippet_chars,
                    'openai_timeout_s', openai_timeout_s
                )
            )
        )
        WHERE config_version_history IS NULL
    """)
    
    # Step 3: Add chatbot_config_version_index to conversations
    op.add_column(
        'conversations',
        sa.Column('chatbot_config_version_index', sa.Integer(), nullable=True)
    )
    
    # Step 4: Migrate existing conversations to use version_index = 0
    # All existing conversations should use version 0 (the initial version)
    op.execute("""
        UPDATE conversations
        SET chatbot_config_version_index = 0
        WHERE chatbot_config_version_index IS NULL
    """)
    
    # Step 5: Remove chatbot_config_snapshot column from conversations
    op.drop_column('conversations', 'chatbot_config_snapshot')
    
    # Step 6: Remove redundant config columns from chatbot_configs
    # All config is now stored in config_version_history JSON, so remove individual columns
    op.drop_column('chatbot_configs', 'system_prompt')
    op.drop_column('chatbot_configs', 'chat_model_id')
    op.drop_column('chatbot_configs', 'embedding_model_id')
    op.drop_column('chatbot_configs', 'vec_db_id')
    op.drop_column('chatbot_configs', 'search_type')
    op.drop_column('chatbot_configs', 'threshold_value')
    op.drop_column('chatbot_configs', 'temperature')
    op.drop_column('chatbot_configs', 'chat_max_tokens')
    op.drop_column('chatbot_configs', 'rag_top_k')
    op.drop_column('chatbot_configs', 'rag_max_history')
    op.drop_column('chatbot_configs', 'rag_context_chars')
    op.drop_column('chatbot_configs', 'rag_snippet_chars')
    op.drop_column('chatbot_configs', 'openai_timeout_s')
    op.drop_column('chatbot_configs', 'chunk_size')
    op.drop_column('chatbot_configs', 'chunk_overlap')
    op.drop_column('chatbot_configs', 'vector_store_type')


def downgrade():
    # Step 1: Re-add redundant config columns to chatbot_configs
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
    
    # Step 2: Migrate data from latest version in history back to columns
    op.execute("""
        UPDATE chatbot_configs
        SET 
            system_prompt = (config_version_history->-1->'config'->>'system_prompt')::text,
            chat_model_id = ((config_version_history->-1->'config'->>'chat_model_id')::bigint),
            embedding_model_id = ((config_version_history->-1->'config'->>'embedding_model_id')::bigint),
            vec_db_id = ((config_version_history->-1->'config'->>'vec_db_id')::bigint),
            search_type = (config_version_history->-1->'config'->>'search_type'),
            threshold_value = ((config_version_history->-1->'config'->>'threshold_value')::float),
            temperature = ((config_version_history->-1->'config'->>'temperature')::float),
            chat_max_tokens = ((config_version_history->-1->'config'->>'chat_max_tokens')::integer),
            rag_top_k = ((config_version_history->-1->'config'->>'rag_top_k')::integer),
            rag_max_history = ((config_version_history->-1->'config'->>'rag_max_history')::integer),
            rag_context_chars = ((config_version_history->-1->'config'->>'rag_context_chars')::integer),
            rag_snippet_chars = ((config_version_history->-1->'config'->>'rag_snippet_chars')::integer),
            openai_timeout_s = ((config_version_history->-1->'config'->>'openai_timeout_s')::integer),
            chunk_size = ((config_version_history->-1->'config'->>'chunk_size')::bigint),
            chunk_overlap = ((config_version_history->-1->'config'->>'chunk_overlap')::bigint),
            vector_store_type = (config_version_history->-1->'config'->>'vector_store_type')
        WHERE config_version_history IS NOT NULL 
        AND jsonb_array_length(config_version_history) > 0
    """)
    
    # Step 3: Re-add chatbot_config_snapshot column
    op.add_column(
        'conversations',
        sa.Column('chatbot_config_snapshot', postgresql.JSONB(astext_type=sa.Text()), nullable=True)
    )
    
    # Step 4: Migrate version_index back to snapshot (use version 0 config)
    # Extract version 0 config from history and store as snapshot
    op.execute("""
        UPDATE conversations c
        SET chatbot_config_snapshot = (
            SELECT cc.config_version_history->0->'config'
            FROM chatbot_configs cc
            WHERE cc.id = c.chatbot_config_id
            AND c.chatbot_config_version_index = 0
        )
        WHERE c.chatbot_config_version_index = 0
    """)
    
    # Step 5: Remove chatbot_config_version_index column
    op.drop_column('conversations', 'chatbot_config_version_index')
    
    # Step 6: Remove config_version_history column
    op.drop_column('chatbot_configs', 'config_version_history')

