"""Media ingest: transcribing stage + per-tenant transcription usage

Revision ID: 20260731_000001
Revises: e40a3d4fec9c
Create Date: 2026-07-31

Two changes, both prerequisites for tenant-uploaded audio/video:

1. A ``transcribing`` value on the processingstage enum. Media is transcribed
   before the document pipeline runs, and that step takes minutes — without a
   stage of its own the progress UI shows nothing for the longest part of the
   job.

2. A ``media_transcription_usage`` table backing the per-tenant monthly
   budget. Transcription is billed per audio-hour, so an open upload form
   without this is uncapped spend.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260731_000001"
down_revision: str | None = "e40a3d4fec9c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # --- 1. processingstage enum ---
    # ADD VALUE IF NOT EXISTS is idempotent, which matters because enum values
    # cannot be dropped: a failed-then-retried migration must not error here.
    # Postgres 12+ allows this inside a transaction.
    op.execute("ALTER TYPE processingstage ADD VALUE IF NOT EXISTS 'transcribing'")

    # --- 2. media_transcription_usage ---
    op.create_table(
        "media_transcription_usage",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("tenant_id", sa.BigInteger(), nullable=False),
        sa.Column("document_id", sa.BigInteger(), nullable=True),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("provider", sa.String(length=64), nullable=True),
        sa.Column("speech_model", sa.String(length=64), nullable=True),
        sa.Column(
            "duration_seconds", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column(
            "billable", sa.Boolean(), nullable=False, server_default=sa.true()
        ),
        sa.Column("estimated_cost_usd", sa.DECIMAL(precision=12, scale=6), nullable=True),
        sa.Column("usage_month", sa.Date(), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        # SET NULL, not CASCADE: deleting a document must not erase the record
        # of what was paid to transcribe it.
        sa.ForeignKeyConstraint(["document_id"], ["documents.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_media_transcription_usage_id"),
        "media_transcription_usage",
        ["id"],
    )
    op.create_index(
        op.f("ix_media_transcription_usage_tenant_id"),
        "media_transcription_usage",
        ["tenant_id"],
    )
    op.create_index(
        op.f("ix_media_transcription_usage_document_id"),
        "media_transcription_usage",
        ["document_id"],
    )
    op.create_index(
        op.f("ix_media_transcription_usage_source"),
        "media_transcription_usage",
        ["source"],
    )
    op.create_index(
        op.f("ix_media_transcription_usage_billable"),
        "media_transcription_usage",
        ["billable"],
    )
    op.create_index(
        op.f("ix_media_transcription_usage_usage_month"),
        "media_transcription_usage",
        ["usage_month"],
    )
    # Composite index serving the quota SUM, which runs on every media ingest.
    op.create_index(
        "idx_media_usage_quota",
        "media_transcription_usage",
        ["tenant_id", "usage_month", "billable"],
    )


def downgrade() -> None:
    op.drop_index("idx_media_usage_quota", table_name="media_transcription_usage")
    op.drop_index(
        op.f("ix_media_transcription_usage_usage_month"),
        table_name="media_transcription_usage",
    )
    op.drop_index(
        op.f("ix_media_transcription_usage_billable"),
        table_name="media_transcription_usage",
    )
    op.drop_index(
        op.f("ix_media_transcription_usage_source"),
        table_name="media_transcription_usage",
    )
    op.drop_index(
        op.f("ix_media_transcription_usage_document_id"),
        table_name="media_transcription_usage",
    )
    op.drop_index(
        op.f("ix_media_transcription_usage_tenant_id"),
        table_name="media_transcription_usage",
    )
    op.drop_index(
        op.f("ix_media_transcription_usage_id"),
        table_name="media_transcription_usage",
    )
    op.drop_table("media_transcription_usage")
    # The 'transcribing' enum value is deliberately NOT removed. Postgres has
    # no DROP VALUE, and the rebuild-the-type dance would fail against any row
    # that still uses it. An unused enum value is harmless.
