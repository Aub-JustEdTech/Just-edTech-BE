"""Initial complete schema - replicates entire local database

This migration creates all 23 application tables with exact schema from local database.
Tables: tenants, roles, users, api_keys, llm_models, tenant_configs, performance_metrics,
        monitoring, billing, documents, conversations, messages, citations, feedback,
        invitations, chat_consumers, conversation_documents, upload_batches,
        document_processing_jobs, document_processing_stages, signups,
        daily_token_usage, monthly_billing

Revision ID: 20251104_initial_complete_schema
Revises: 
Create Date: 2025-11-04

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = '20251104_initial_complete_schema'
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    # Create custom enum types
    processingstatus = postgresql.ENUM('pending', 'processing', 'completed', 'failed', name='processingstatus')
    processingstatus.create(op.get_bind())
    
    jobstatus = postgresql.ENUM('pending', 'processing', 'completed', 'failed', name='jobstatus')
    jobstatus.create(op.get_bind())
    
    processingstage = postgresql.ENUM('pending', 'downloading', 'extracting', 'chunking', 'embedding', 'storing', 'completed', 'failed', name='processingstage')
    processingstage.create(op.get_bind())
    
    stagestatus = postgresql.ENUM('pending', 'in_progress', 'completed', 'failed', 'retrying', name='stagestatus')
    stagestatus.create(op.get_bind())
    
    batchstatus = postgresql.ENUM('pending', 'processing', 'completed', 'partial_success', 'failed', name='batchstatus')
    batchstatus.create(op.get_bind())
    
    # ====================================================================
    # 1. TENANTS TABLE - Base table, no foreign keys
    # ====================================================================
    op.create_table(
        'tenants',
        sa.Column('id', sa.BigInteger(), nullable=False),
        sa.Column('name', sa.String(), nullable=False),
        sa.Column('domain', sa.String(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.Column('logo_url', sa.String(), nullable=True),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_tenants_id', 'tenants', ['id'])
    op.create_index('ix_tenants_name', 'tenants', ['name'], unique=True)
    op.create_index('ix_tenants_domain', 'tenants', ['domain'], unique=True)
    
    # ====================================================================
    # 2. ROLES TABLE - Base table, no foreign keys
    # ====================================================================
    op.create_table(
        'roles',
        sa.Column('id', sa.BigInteger(), nullable=False),
        sa.Column('name', sa.String(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_roles_id', 'roles', ['id'])
    op.create_index('ix_roles_name', 'roles', ['name'], unique=True)
    
    # ====================================================================
    # 3. USERS TABLE - Depends on: tenants, roles
    # ====================================================================
    op.create_table(
        'users',
        sa.Column('id', sa.BigInteger(), nullable=False),
        sa.Column('email', sa.String(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.Column('tenant_id', sa.BigInteger(), nullable=False),
        sa.Column('name', sa.String(), nullable=True),
        sa.Column('password_hash', sa.String(), nullable=False),
        sa.Column('role_id', sa.BigInteger(), nullable=True),
        sa.Column('email_verified', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('verified_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['role_id'], ['roles.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_users_id', 'users', ['id'])
    op.create_index('ix_users_email', 'users', ['email'], unique=True)
    
    # ====================================================================
    # 4. API_KEYS TABLE - Depends on: tenants
    # ====================================================================
    op.create_table(
        'api_keys',
        sa.Column('id', sa.BigInteger(), nullable=False),
        sa.Column('tenant_id', sa.BigInteger(), nullable=False),
        sa.Column('key', sa.String(), nullable=False),
        sa.Column('secret', sa.String(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_api_keys_id', 'api_keys', ['id'])
    op.create_index('ix_api_keys_key', 'api_keys', ['key'], unique=True)
    
    # ====================================================================
    # 5. LLM_MODELS TABLE - Depends on: tenants
    # ====================================================================
    op.create_table(
        'llm_models',
        sa.Column('id', sa.BigInteger(), nullable=False),
        sa.Column('name', sa.String(), nullable=False),
        sa.Column('provider', sa.String(), nullable=False),
        sa.Column('config', postgresql.JSON(astext_type=sa.Text()), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.Column('tenant_id', sa.BigInteger(), nullable=False),
        sa.Column('input_token_price', sa.DECIMAL(precision=10, scale=6), nullable=True, server_default='0.0'),
        sa.Column('output_token_price', sa.DECIMAL(precision=10, scale=6), nullable=True, server_default='0.0'),
        sa.Column('cache_token_price', sa.DECIMAL(precision=10, scale=6), nullable=True, server_default='0.0'),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_llm_models_id', 'llm_models', ['id'])
    
    # ====================================================================
    # 6. TENANT_CONFIGS TABLE - Depends on: tenants, llm_models
    # ====================================================================
    op.create_table(
        'tenant_configs',
        sa.Column('id', sa.BigInteger(), nullable=False),
        sa.Column('tenant_id', sa.BigInteger(), nullable=False),
        sa.Column('system_prompt', sa.Text(), nullable=True),
        sa.Column('chat_model_id', sa.BigInteger(), nullable=True),
        sa.Column('embedding_model_id', sa.BigInteger(), nullable=True),
        sa.Column('vec_db_id', sa.BigInteger(), nullable=True),
        sa.Column('search_type', sa.String(), nullable=True),
        sa.Column('threshold_value', sa.Float(), nullable=True),
        sa.Column('temperature', sa.Float(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.Column('chunk_size', sa.BigInteger(), nullable=False),
        sa.Column('chunk_overlap', sa.BigInteger(), nullable=False),
        sa.Column('vector_store_type', sa.String(), nullable=False),
        sa.Column('chat_max_tokens', sa.Integer(), nullable=True),
        sa.Column('rag_top_k', sa.Integer(), nullable=True),
        sa.Column('rag_max_history', sa.Integer(), nullable=True),
        sa.Column('rag_context_chars', sa.Integer(), nullable=True),
        sa.Column('rag_snippet_chars', sa.Integer(), nullable=True),
        sa.Column('openai_timeout_s', sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['chat_model_id'], ['llm_models.id'], ondelete='NO ACTION'),
        sa.ForeignKeyConstraint(['embedding_model_id'], ['llm_models.id'], ondelete='NO ACTION'),
        sa.ForeignKeyConstraint(['vec_db_id'], ['llm_models.id'], ondelete='NO ACTION'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_tenant_configs_id', 'tenant_configs', ['id'])
    
    # ====================================================================
    # 7. PERFORMANCE_METRICS TABLE - Depends on: tenant_configs
    # ====================================================================
    op.create_table(
        'performance_metrics',
        sa.Column('id', sa.BigInteger(), nullable=False),
        sa.Column('retrieval_recall', sa.Float(), nullable=True),
        sa.Column('llm_eval', sa.Float(), nullable=True),
        sa.Column('bleu', sa.Float(), nullable=True),
        sa.Column('accuracy', sa.Float(), nullable=True),
        sa.Column('precision', sa.Float(), nullable=True),
        sa.Column('f1_score', sa.Float(), nullable=True),
        sa.Column('confidence_score', sa.Float(), nullable=True),
        sa.Column('tenant_config_id', sa.BigInteger(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['tenant_config_id'], ['tenant_configs.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_performance_metrics_id', 'performance_metrics', ['id'])
    
    # ====================================================================
    # 8. MONITORING TABLE - Depends on: tenants, tenant_configs
    # ====================================================================
    op.create_table(
        'monitoring',
        sa.Column('id', sa.BigInteger(), nullable=False),
        sa.Column('tenant_id', sa.BigInteger(), nullable=False),
        sa.Column('tenant_config_id', sa.BigInteger(), nullable=True),
        sa.Column('logs', sa.Text(), nullable=True),
        sa.Column('errors', sa.Text(), nullable=True),
        sa.Column('tokens_count', sa.Text(), nullable=True),
        sa.Column('request_count', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['tenant_config_id'], ['tenant_configs.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_monitoring_id', 'monitoring', ['id'])
    
    # ====================================================================
    # 9. BILLING TABLE - Depends on: tenants
    # ====================================================================
    op.create_table(
        'billing',
        sa.Column('id', sa.BigInteger(), nullable=False),
        sa.Column('tenant_id', sa.BigInteger(), nullable=False),
        sa.Column('amount', sa.DECIMAL(), nullable=False),
        sa.Column('status', sa.String(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_billing_id', 'billing', ['id'])
    
    # ====================================================================
    # 10. INVITATIONS TABLE - Depends on: tenants, roles
    # ====================================================================
    op.create_table(
        'invitations',
        sa.Column('id', sa.BigInteger(), nullable=False),
        sa.Column('tenant_id', sa.BigInteger(), nullable=False),
        sa.Column('email', sa.String(), nullable=False),
        sa.Column('role_id', sa.BigInteger(), nullable=True),
        sa.Column('token', sa.String(), nullable=False),
        sa.Column('accepted', sa.Boolean(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['role_id'], ['roles.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_invitations_id', 'invitations', ['id'])
    op.create_index('ix_invitations_email', 'invitations', ['email'])
    op.create_index('ix_invitations_token', 'invitations', ['token'], unique=True)
    
    # ====================================================================
    # 11. SIGNUPS TABLE - No foreign keys
    # ====================================================================
    op.create_table(
        'signups',
        sa.Column('email', sa.String(), nullable=False),
        sa.Column('name', sa.String(), nullable=True),
        sa.Column('password_hash', sa.String(), nullable=False),
        sa.Column('expires_at', sa.DateTime(), nullable=True),
        sa.Column('is_verified', sa.Boolean(), nullable=False),
        sa.Column('verified_at', sa.DateTime(), nullable=True),
        sa.Column('id', sa.BigInteger(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_signups_id', 'signups', ['id'])
    op.create_index('ix_signups_email', 'signups', ['email'], unique=True)
    
    # ====================================================================
    # 12. CHAT_CONSUMERS TABLE - Depends on: tenants
    # ====================================================================
    op.create_table(
        'chat_consumers',
        sa.Column('chat_consumer_uuid', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('tenant_id', sa.BigInteger(), nullable=False),
        sa.Column('id', sa.BigInteger(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_chat_consumers_id', 'chat_consumers', ['id'])
    op.create_index('ix_chat_consumers_chat_consumer_uuid', 'chat_consumers', ['chat_consumer_uuid'], unique=True)
    
    # ====================================================================
    # 13. UPLOAD_BATCHES TABLE - Depends on: tenants
    # ====================================================================
    op.create_table(
        'upload_batches',
        sa.Column('batch_id', sa.String(), nullable=False),
        sa.Column('tenant_id', sa.BigInteger(), nullable=False),
        sa.Column('total_documents', sa.Integer(), nullable=False),
        sa.Column('completed_documents', sa.Integer(), nullable=False),
        sa.Column('failed_documents', sa.Integer(), nullable=False),
        sa.Column('processing_documents', sa.Integer(), nullable=False),
        sa.Column('pending_documents', sa.Integer(), nullable=False),
        sa.Column('status', postgresql.ENUM('pending', 'processing', 'completed', 'partial_success', 'failed', name='batchstatus', create_type=False), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('error_summary', sa.Text(), nullable=True),
        sa.Column('id', sa.BigInteger(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_upload_batches_id', 'upload_batches', ['id'])
    op.create_index('ix_upload_batches_batch_id', 'upload_batches', ['batch_id'], unique=True)
    op.create_index('ix_upload_batches_tenant_id', 'upload_batches', ['tenant_id'])
    op.create_index('ix_upload_batches_status', 'upload_batches', ['status'])
    
    # ====================================================================
    # 14. DOCUMENTS TABLE - Depends on: tenants, upload_batches
    # ====================================================================
    op.create_table(
        'documents',
        sa.Column('id', sa.BigInteger(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.Column('name', sa.String(), nullable=False),
        sa.Column('doc_id', sa.String(), nullable=False),
        sa.Column('s3_url', sa.String(), nullable=True),
        sa.Column('tenant_id', sa.BigInteger(), nullable=False),
        sa.Column('document_type', sa.String(), nullable=False),
        sa.Column('processing_status', postgresql.ENUM('pending', 'processing', 'completed', 'failed', name='processingstatus', create_type=False), nullable=False),
        sa.Column('chunk_count', sa.Integer(), nullable=False),
        sa.Column('file_size_bytes', sa.Integer(), nullable=True),
        sa.Column('error_message', sa.String(), nullable=True),
        sa.Column('upload_batch_id', sa.BigInteger(), nullable=True),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['upload_batch_id'], ['upload_batches.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_documents_id', 'documents', ['id'])
    op.create_index('ix_documents_doc_id', 'documents', ['doc_id'], unique=True)
    op.create_index('ix_documents_s3_url', 'documents', ['s3_url'], unique=True)
    op.create_index('ix_documents_tenant_id', 'documents', ['tenant_id'])
    op.create_index('ix_documents_processing_status', 'documents', ['processing_status'])
    op.create_index('ix_documents_upload_batch_id', 'documents', ['upload_batch_id'])
    
    # ====================================================================
    # 15. CONVERSATIONS TABLE - Depends on: tenants, users, chat_consumers
    # ====================================================================
    op.create_table(
        'conversations',
        sa.Column('id', sa.BigInteger(), nullable=False),
        sa.Column('user_id', sa.BigInteger(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.Column('tenant_id', sa.BigInteger(), nullable=False),
        sa.Column('title', sa.String(length=255), nullable=True),
        sa.Column('chat_consumer_id', sa.BigInteger(), nullable=True),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['chat_consumer_id'], ['chat_consumers.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_conversations_id', 'conversations', ['id'])
    
    # ====================================================================
    # 16. MESSAGES TABLE - Depends on: conversations
    # ====================================================================
    op.create_table(
        'messages',
        sa.Column('conversation_id', sa.BigInteger(), nullable=False),
        sa.Column('role', sa.String(length=50), nullable=False),
        sa.Column('content', sa.Text(), nullable=False),
        sa.Column('id', sa.BigInteger(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.Column('input_tokens', sa.Integer(), nullable=True),
        sa.Column('output_tokens', sa.Integer(), nullable=True),
        sa.Column('total_tokens', sa.Integer(), nullable=True),
        sa.Column('model_used', sa.String(length=100), nullable=True),
        sa.ForeignKeyConstraint(['conversation_id'], ['conversations.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_messages_id', 'messages', ['id'])
    
    # ====================================================================
    # 17. CITATIONS TABLE - Depends on: messages
    # ====================================================================
    op.create_table(
        'citations',
        sa.Column('message_id', sa.BigInteger(), nullable=False),
        sa.Column('document_title', sa.String(length=500), nullable=True),
        sa.Column('document_url', sa.Text(), nullable=False),
        sa.Column('snippet', sa.Text(), nullable=True),
        sa.Column('position', sa.Integer(), nullable=True),
        sa.Column('id', sa.BigInteger(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['message_id'], ['messages.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_citations_id', 'citations', ['id'])
    
    # ====================================================================
    # 18. FEEDBACK TABLE - Depends on: conversations
    # ====================================================================
    op.create_table(
        'feedback',
        sa.Column('id', sa.BigInteger(), nullable=False),
        sa.Column('c_id', sa.BigInteger(), nullable=False),
        sa.Column('messages', sa.Text(), nullable=True),
        sa.Column('feedback', sa.Text(), nullable=True),
        sa.Column('is_positive', sa.Boolean(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['c_id'], ['conversations.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_feedback_id', 'feedback', ['id'])
    
    # ====================================================================
    # 19. CONVERSATION_DOCUMENTS TABLE - Depends on: conversations, documents
    # ====================================================================
    op.create_table(
        'conversation_documents',
        sa.Column('conversation_id', sa.BigInteger(), nullable=False),
        sa.Column('document_id', sa.BigInteger(), nullable=False),
        sa.ForeignKeyConstraint(['conversation_id'], ['conversations.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['document_id'], ['documents.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('conversation_id', 'document_id'),
        sa.UniqueConstraint('conversation_id', 'document_id', name='uq_conversation_document')
    )
    
    # ====================================================================
    # 20. DOCUMENT_PROCESSING_JOBS TABLE - Depends on: documents
    # ====================================================================
    op.create_table(
        'document_processing_jobs',
        sa.Column('document_id', sa.BigInteger(), nullable=False),
        sa.Column('status', postgresql.ENUM('pending', 'processing', 'completed', 'failed', name='jobstatus', create_type=False), nullable=False),
        sa.Column('processor_type', sa.String(), nullable=False),
        sa.Column('error_message', sa.Text(), nullable=True),
        sa.Column('chunks_created', sa.Integer(), nullable=False),
        sa.Column('processing_time_seconds', sa.Float(), nullable=True),
        sa.Column('id', sa.BigInteger(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['document_id'], ['documents.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_document_processing_jobs_id', 'document_processing_jobs', ['id'])
    op.create_index('ix_document_processing_jobs_document_id', 'document_processing_jobs', ['document_id'])
    op.create_index('ix_document_processing_jobs_status', 'document_processing_jobs', ['status'])
    
    # ====================================================================
    # 21. DOCUMENT_PROCESSING_STAGES TABLE - Depends on: documents, document_processing_jobs
    # ====================================================================
    op.create_table(
        'document_processing_stages',
        sa.Column('document_id', sa.BigInteger(), nullable=False),
        sa.Column('job_id', sa.BigInteger(), nullable=False),
        sa.Column('stage', postgresql.ENUM('pending', 'downloading', 'extracting', 'chunking', 'embedding', 'storing', 'completed', 'failed', name='processingstage', create_type=False), nullable=False),
        sa.Column('status', postgresql.ENUM('pending', 'in_progress', 'completed', 'failed', 'retrying', name='stagestatus', create_type=False), nullable=False),
        sa.Column('started_at', sa.DateTime(), nullable=True),
        sa.Column('completed_at', sa.DateTime(), nullable=True),
        sa.Column('duration_seconds', sa.Float(), nullable=True),
        sa.Column('error_message', sa.Text(), nullable=True),
        sa.Column('error_traceback', sa.Text(), nullable=True),
        sa.Column('retry_count', sa.Integer(), nullable=False),
        sa.Column('input_size', sa.Integer(), nullable=True),
        sa.Column('output_size', sa.Integer(), nullable=True),
        sa.Column('stage_metadata', sa.Text(), nullable=True),
        sa.Column('id', sa.BigInteger(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['document_id'], ['documents.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['job_id'], ['document_processing_jobs.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_document_processing_stages_id', 'document_processing_stages', ['id'])
    op.create_index('ix_document_processing_stages_document_id', 'document_processing_stages', ['document_id'])
    op.create_index('ix_document_processing_stages_job_id', 'document_processing_stages', ['job_id'])
    op.create_index('ix_document_processing_stages_stage', 'document_processing_stages', ['stage'])
    op.create_index('ix_document_processing_stages_status', 'document_processing_stages', ['status'])
    
    # ====================================================================
    # 22. DAILY_TOKEN_USAGE TABLE - Depends on: tenants
    # ====================================================================
    op.create_table(
        'daily_token_usage',
        sa.Column('usage_date', sa.Date(), nullable=False),
        sa.Column('tenant_id', sa.BigInteger(), nullable=False),
        sa.Column('model_name', sa.String(length=100), nullable=False),
        sa.Column('total_input_tokens', sa.BigInteger(), nullable=False),
        sa.Column('total_output_tokens', sa.BigInteger(), nullable=False),
        sa.Column('total_tokens', sa.BigInteger(), nullable=False),
        sa.Column('total_cache_tokens', sa.BigInteger(), nullable=False),
        sa.Column('message_count', sa.BigInteger(), nullable=False),
        sa.Column('input_token_cost', sa.DECIMAL(precision=12, scale=6), nullable=True),
        sa.Column('output_token_cost', sa.DECIMAL(precision=12, scale=6), nullable=True),
        sa.Column('cache_token_cost', sa.DECIMAL(precision=12, scale=6), nullable=True),
        sa.Column('total_cost', sa.DECIMAL(precision=12, scale=6), nullable=True),
        sa.Column('id', sa.BigInteger(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('tenant_id', 'model_name', 'usage_date', name='uq_tenant_model_date')
    )
    op.create_index('ix_daily_token_usage_id', 'daily_token_usage', ['id'])
    op.create_index('ix_daily_token_usage_usage_date', 'daily_token_usage', ['usage_date'])
    op.create_index('ix_daily_token_usage_tenant_id', 'daily_token_usage', ['tenant_id'])
    op.create_index('ix_daily_token_usage_model_name', 'daily_token_usage', ['model_name'])
    op.create_index('idx_daily_usage_lookup', 'daily_token_usage', ['tenant_id', 'usage_date', 'model_name'])
    
    # ====================================================================
    # 23. MONTHLY_BILLING TABLE - Depends on: tenants
    # ====================================================================
    op.create_table(
        'monthly_billing',
        sa.Column('billing_year', sa.Integer(), nullable=False),
        sa.Column('billing_month', sa.Integer(), nullable=False),
        sa.Column('period_start_date', sa.Date(), nullable=False),
        sa.Column('period_end_date', sa.Date(), nullable=False),
        sa.Column('tenant_id', sa.BigInteger(), nullable=False),
        sa.Column('model_name', sa.String(length=100), nullable=False),
        sa.Column('total_input_tokens', sa.BigInteger(), nullable=False),
        sa.Column('total_output_tokens', sa.BigInteger(), nullable=False),
        sa.Column('total_tokens', sa.BigInteger(), nullable=False),
        sa.Column('total_cache_tokens', sa.BigInteger(), nullable=False),
        sa.Column('message_count', sa.BigInteger(), nullable=False),
        sa.Column('input_token_cost', sa.DECIMAL(precision=12, scale=6), nullable=False),
        sa.Column('output_token_cost', sa.DECIMAL(precision=12, scale=6), nullable=False),
        sa.Column('cache_token_cost', sa.DECIMAL(precision=12, scale=6), nullable=False),
        sa.Column('total_cost', sa.DECIMAL(precision=12, scale=6), nullable=False),
        sa.Column('avg_input_token_price', sa.DECIMAL(precision=10, scale=6), nullable=True),
        sa.Column('avg_output_token_price', sa.DECIMAL(precision=10, scale=6), nullable=True),
        sa.Column('avg_cache_token_price', sa.DECIMAL(precision=10, scale=6), nullable=True),
        sa.Column('id', sa.BigInteger(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('tenant_id', 'model_name', 'billing_year', 'billing_month', name='uq_tenant_model_month')
    )
    op.create_index('ix_monthly_billing_id', 'monthly_billing', ['id'])
    op.create_index('ix_monthly_billing_billing_year', 'monthly_billing', ['billing_year'])
    op.create_index('ix_monthly_billing_billing_month', 'monthly_billing', ['billing_month'])
    op.create_index('ix_monthly_billing_tenant_id', 'monthly_billing', ['tenant_id'])
    op.create_index('ix_monthly_billing_model_name', 'monthly_billing', ['model_name'])
    op.create_index('idx_monthly_billing_lookup', 'monthly_billing', ['tenant_id', 'billing_year', 'billing_month'])
    op.create_index('idx_monthly_billing_date_range', 'monthly_billing', ['period_start_date', 'period_end_date'])


def downgrade():
    # Drop all tables in reverse order
    op.drop_table('monthly_billing')
    op.drop_table('daily_token_usage')
    op.drop_table('document_processing_stages')
    op.drop_table('document_processing_jobs')
    op.drop_table('conversation_documents')
    op.drop_table('feedback')
    op.drop_table('citations')
    op.drop_table('messages')
    op.drop_table('conversations')
    op.drop_table('documents')
    op.drop_table('upload_batches')
    op.drop_table('chat_consumers')
    op.drop_table('signups')
    op.drop_table('invitations')
    op.drop_table('billing')
    op.drop_table('monitoring')
    op.drop_table('performance_metrics')
    op.drop_table('tenant_configs')
    op.drop_table('llm_models')
    op.drop_table('api_keys')
    op.drop_table('users')
    op.drop_table('roles')
    op.drop_table('tenants')
    
    # Drop custom enum types
    sa.Enum(name='batchstatus').drop(op.get_bind())
    sa.Enum(name='stagestatus').drop(op.get_bind())
    sa.Enum(name='processingstage').drop(op.get_bind())
    sa.Enum(name='jobstatus').drop(op.get_bind())
    sa.Enum(name='processingstatus').drop(op.get_bind())

