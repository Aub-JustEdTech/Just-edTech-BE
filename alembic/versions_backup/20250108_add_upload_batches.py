"""add upload batches for bulk upload tracking

Revision ID: 20250108_add_upload_batches
Revises: aa6f53e5fff9
Create Date: 2025-01-08 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '20250108_add_upload_batches'
down_revision: Union[str, None] = 'aa6f53e5fff9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Create batch_status enum if it doesn't exist
    conn = op.get_bind()
    conn.execute(sa.text("""
        DO $$ BEGIN
            CREATE TYPE batchstatus AS ENUM ('pending', 'processing', 'completed', 'partial_success', 'failed');
        EXCEPTION
            WHEN duplicate_object THEN null;
        END $$;
    """))
    
    # Create upload_batches table
    conn.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS upload_batches (
            id BIGSERIAL PRIMARY KEY,
            batch_id VARCHAR NOT NULL,
            tenant_id BIGINT NOT NULL,
            total_documents INTEGER NOT NULL DEFAULT 0,
            completed_documents INTEGER NOT NULL DEFAULT 0,
            failed_documents INTEGER NOT NULL DEFAULT 0,
            processing_documents INTEGER NOT NULL DEFAULT 0,
            pending_documents INTEGER NOT NULL DEFAULT 0,
            status batchstatus NOT NULL DEFAULT 'pending',
            description TEXT,
            error_summary TEXT,
            created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
            FOREIGN KEY (tenant_id) REFERENCES tenants(id) ON DELETE CASCADE
        );
    """))
    
    # Create indexes if they don't exist
    conn.execute(sa.text("CREATE UNIQUE INDEX IF NOT EXISTS ix_upload_batches_batch_id ON upload_batches(batch_id);"))
    conn.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_upload_batches_tenant_id ON upload_batches(tenant_id);"))
    conn.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_upload_batches_status ON upload_batches(status);"))
    
    # Add upload_batch_id column to documents table if it doesn't exist
    conn.execute(sa.text("""
        DO $$ BEGIN
            ALTER TABLE documents ADD COLUMN upload_batch_id BIGINT REFERENCES upload_batches(id) ON DELETE SET NULL;
        EXCEPTION
            WHEN duplicate_column THEN null;
        END $$;
    """))
    
    # Create index on documents.upload_batch_id if it doesn't exist
    conn.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_documents_upload_batch_id ON documents(upload_batch_id);"))


def downgrade() -> None:
    # Drop index on documents.upload_batch_id
    op.drop_index('ix_documents_upload_batch_id', table_name='documents')
    
    # Drop foreign key constraint
    op.drop_constraint('fk_documents_upload_batch_id', 'documents', type_='foreignkey')
    
    # Drop upload_batch_id column from documents
    op.drop_column('documents', 'upload_batch_id')
    
    # Drop indexes on upload_batches
    op.drop_index('ix_upload_batches_status', table_name='upload_batches')
    op.drop_index('ix_upload_batches_tenant_id', table_name='upload_batches')
    op.drop_index('ix_upload_batches_batch_id', table_name='upload_batches')
    
    # Drop upload_batches table
    op.drop_table('upload_batches')
    
    # Drop batch_status enum
    sa.Enum(name='batchstatus').drop(op.get_bind())

