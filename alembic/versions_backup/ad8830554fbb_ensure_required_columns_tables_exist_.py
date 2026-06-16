"""ensure required columns/tables exist idempotently

Revision ID: ad8830554fbb
Revises: b780070b26ff
Create Date: 2025-10-13 14:18:57.009751

"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "ad8830554fbb"
down_revision: str | None = "b780070b26ff"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    conn = op.get_bind()

    # 1) conversations.title (from 20251006_174701)
    conn.execute(
        sa.text(
            """
            ALTER TABLE IF EXISTS conversations
            ADD COLUMN IF NOT EXISTS title VARCHAR(255);
            """
        )
    )

    # 2) citations.updated_at (from 536288a932ce)
    # Ensure column exists with NOT NULL and default now()
    conn.execute(
        sa.text(
            """
            ALTER TABLE IF EXISTS citations
            ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW();
            """
        )
    )

    # 3) documents columns (from 04d537f99d07)
    # Ensure processingstatus enum exists (uses uppercase variants per prior migration)
    conn.execute(
        sa.text(
            """
            DO $$ BEGIN
                IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'processingstatus') THEN
                    CREATE TYPE processingstatus AS ENUM ('PENDING','PROCESSING','COMPLETED','FAILED');
                END IF;
            END $$;
            """
        )
    )

    # Add missing document columns
    conn.execute(
        sa.text(
            """
            ALTER TABLE IF EXISTS documents
            ADD COLUMN IF NOT EXISTS tenant_id BIGINT,
            ADD COLUMN IF NOT EXISTS document_type VARCHAR,
            ADD COLUMN IF NOT EXISTS processing_status processingstatus DEFAULT 'PENDING' NOT NULL,
            ADD COLUMN IF NOT EXISTS chunk_count INTEGER DEFAULT 0 NOT NULL,
            ADD COLUMN IF NOT EXISTS file_size_bytes INTEGER,
            ADD COLUMN IF NOT EXISTS error_message VARCHAR;
            """
        )
    )

    # Ensure indexes/constraints on documents
    conn.execute(
        sa.text(
            "CREATE INDEX IF NOT EXISTS ix_documents_tenant_id ON documents(tenant_id);"
        )
    )
    conn.execute(
        sa.text(
            "CREATE INDEX IF NOT EXISTS ix_documents_processing_status ON documents(processing_status);"
        )
    )
    # Foreign key for tenant_id if absent
    conn.execute(
        sa.text(
            """
            DO $$ BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM information_schema.table_constraints tc
                    WHERE tc.constraint_type = 'FOREIGN KEY'
                      AND tc.table_name = 'documents'
                      AND tc.constraint_name = 'documents_tenant_id_fkey'
                ) THEN
                    ALTER TABLE documents
                    ADD CONSTRAINT documents_tenant_id_fkey FOREIGN KEY (tenant_id)
                    REFERENCES tenants(id) ON DELETE CASCADE;
                END IF;
            END $$;
            """
        )
    )

    # 4) upload_batches table and documents.upload_batch_id (from 20250108_add_upload_batches)
    # Ensure batchstatus enum exists (lowercase variants per prior migration)
    conn.execute(
        sa.text(
            """
            DO $$ BEGIN
                IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'batchstatus') THEN
                    CREATE TYPE batchstatus AS ENUM ('pending','processing','completed','partial_success','failed');
                END IF;
            END $$;
            """
        )
    )

    # Create upload_batches table if missing
    conn.execute(
        sa.text(
            """
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
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                FOREIGN KEY (tenant_id) REFERENCES tenants(id) ON DELETE CASCADE
            );
            """
        )
    )

    # Indexes on upload_batches
    conn.execute(
        sa.text(
            "CREATE UNIQUE INDEX IF NOT EXISTS ix_upload_batches_batch_id ON upload_batches(batch_id);"
        )
    )
    conn.execute(
        sa.text(
            "CREATE INDEX IF NOT EXISTS ix_upload_batches_tenant_id ON upload_batches(tenant_id);"
        )
    )
    conn.execute(
        sa.text(
            "CREATE INDEX IF NOT EXISTS ix_upload_batches_status ON upload_batches(status);"
        )
    )

    # Add documents.upload_batch_id with FK and index
    conn.execute(
        sa.text(
            """
            ALTER TABLE IF EXISTS documents
            ADD COLUMN IF NOT EXISTS upload_batch_id BIGINT;
            """
        )
    )
    conn.execute(
        sa.text(
            "CREATE INDEX IF NOT EXISTS ix_documents_upload_batch_id ON documents(upload_batch_id);"
        )
    )
    conn.execute(
        sa.text(
            """
            DO $$ BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM information_schema.table_constraints tc
                    WHERE tc.constraint_type = 'FOREIGN KEY'
                      AND tc.table_name = 'documents'
                      AND tc.constraint_name = 'documents_upload_batch_id_fkey'
                ) THEN
                    ALTER TABLE documents
                    ADD CONSTRAINT documents_upload_batch_id_fkey FOREIGN KEY (upload_batch_id)
                    REFERENCES upload_batches(id) ON DELETE SET NULL;
                END IF;
            END $$;
            """
        )
    )


def downgrade() -> None:
    # No-op: This is a safety migration to reconcile schema; not reverting.
    pass
