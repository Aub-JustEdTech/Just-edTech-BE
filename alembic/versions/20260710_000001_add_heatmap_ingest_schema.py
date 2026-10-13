"""add_heatmap_ingest_schema

Adds the heatmap-ingest tables and Document extensions:
  - documents.content_hash / entity_type / doc_kind / meeting_date
  - charter_district_mapping (charter org_code -> parent public district org_code)
  - batch_classification_jobs (OpenAI Batch API job tracking)
  - pending_classifications (per-chunk classification queue)
  - heatmap_aggregate (precomputed per-(school, topic) counts)

Revises: 20260708_000001
Create Date: 2026-07-10 00:00:01
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "20260710_000001"
down_revision: Union[str, None] = "20260708_000001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── 1. Extend documents table ─────────────────────────────────────────
    op.add_column(
        "documents",
        sa.Column("content_hash", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "documents",
        sa.Column("entity_type", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "documents",
        sa.Column("doc_kind", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "documents",
        sa.Column("meeting_date", sa.Date(), nullable=True),
    )
    op.create_index("ix_documents_content_hash", "documents", ["content_hash"])
    op.create_index("ix_documents_entity_type", "documents", ["entity_type"])
    op.create_index("ix_documents_meeting_date", "documents", ["meeting_date"])

    # ── 2. charter_district_mapping ────────────────────────────────────────
    # org_code values reference schools via the (tenant_id, org_code) unique
    # constraint on schools — PostgreSQL requires composite FKs here.
    op.create_table(
        "charter_district_mapping",
        sa.Column("tenant_id", sa.BigInteger(), nullable=False),
        sa.Column("charter_org_code", sa.String(length=16), nullable=False),
        sa.Column("parent_district_org_code", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "charter_org_code"],
            ["schools.tenant_id", "schools.org_code"],
            ondelete="CASCADE",
            name="fk_charter_mapping_charter_school",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "parent_district_org_code"],
            ["schools.tenant_id", "schools.org_code"],
            ondelete="CASCADE",
            name="fk_charter_mapping_parent_district",
        ),
        sa.PrimaryKeyConstraint("tenant_id", "charter_org_code"),
    )
    op.create_index(
        "ix_charter_district_mapping_parent",
        "charter_district_mapping",
        ["tenant_id", "parent_district_org_code"],
    )

    # ── 3. batch_classification_jobs ──────────────────────────────────────
    op.create_table(
        "batch_classification_jobs",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("batch_id", sa.String(length=64), nullable=False),
        sa.Column("input_jsonl_s3_key", sa.Text(), nullable=False),
        sa.Column("output_jsonl_s3_key", sa.Text(), nullable=True),
        sa.Column("chunk_count", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("applied_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("batch_id", name="uq_batch_classification_jobs_batch_id"),
    )
    op.create_index(
        op.f("ix_batch_classification_jobs_id"),
        "batch_classification_jobs",
        ["id"],
    )
    op.create_index(
        "ix_batch_classification_jobs_status",
        "batch_classification_jobs",
        ["status"],
    )

    # ── 4. pending_classifications ─────────────────────────────────────────
    op.create_table(
        "pending_classifications",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("document_id", sa.BigInteger(), nullable=False),
        sa.Column("qdrant_point_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("chunk_text", sa.Text(), nullable=False),
        sa.Column("page_number", sa.Integer(), nullable=True),
        sa.Column("entity_type", sa.String(length=64), nullable=True),
        sa.Column("meeting_date", sa.Date(), nullable=True),
        sa.Column("batch_id", sa.String(length=64), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["document_id"], ["documents.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_pending_classifications_id"),
        "pending_classifications",
        ["id"],
    )
    op.create_index(
        "ix_pending_classifications_status",
        "pending_classifications",
        ["status"],
    )
    op.create_index(
        "ix_pending_classifications_batch",
        "pending_classifications",
        ["batch_id"],
    )
    op.create_index(
        "ix_pending_classifications_doc",
        "pending_classifications",
        ["document_id"],
    )
    op.create_index(
        "ix_pending_classifications_qdrant_point",
        "pending_classifications",
        ["qdrant_point_id"],
    )

    # ── 5. heatmap_aggregate ──────────────────────────────────────────────
    op.create_table(
        "heatmap_aggregate",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("source_id", sa.BigInteger(), nullable=False),
        sa.Column("topic", sa.String(length=64), nullable=False),
        sa.Column("chunk_count", sa.Integer(), nullable=False),
        sa.Column("doc_count", sa.Integer(), nullable=False),
        sa.Column("meeting_count", sa.Integer(), nullable=False),
        sa.Column("last_meeting_date", sa.Date(), nullable=True),
        sa.Column(
            "action_types",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["source_id"], ["schools.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("source_id", "topic"),
    )
    op.create_index(
        "ix_heatmap_aggregate_topic",
        "heatmap_aggregate",
        ["topic"],
    )


def downgrade() -> None:
    op.drop_index("ix_heatmap_aggregate_topic", table_name="heatmap_aggregate")
    op.drop_table("heatmap_aggregate")

    op.drop_index(
        "ix_pending_classifications_qdrant_point",
        table_name="pending_classifications",
    )
    op.drop_index(
        "ix_pending_classifications_doc", table_name="pending_classifications"
    )
    op.drop_index(
        "ix_pending_classifications_batch", table_name="pending_classifications"
    )
    op.drop_index(
        "ix_pending_classifications_status", table_name="pending_classifications"
    )
    op.drop_index(
        op.f("ix_pending_classifications_id"), table_name="pending_classifications"
    )
    op.drop_table("pending_classifications")

    op.drop_index(
        "ix_batch_classification_jobs_status",
        table_name="batch_classification_jobs",
    )
    op.drop_index(
        op.f("ix_batch_classification_jobs_id"),
        table_name="batch_classification_jobs",
    )
    op.drop_table("batch_classification_jobs")

    op.drop_index(
        "ix_charter_district_mapping_parent",
        table_name="charter_district_mapping",
    )
    op.drop_table("charter_district_mapping")

    op.drop_index("ix_documents_meeting_date", table_name="documents")
    op.drop_index("ix_documents_entity_type", table_name="documents")
    op.drop_index("ix_documents_content_hash", table_name="documents")
    op.drop_column("documents", "meeting_date")
    op.drop_column("documents", "doc_kind")
    op.drop_column("documents", "entity_type")
    op.drop_column("documents", "content_hash")
