"""add_heatmap_ingest_v1_metadata

Adds V1 heatmap-ingest metadata columns:
  - schools.state (2-letter abbreviation, default 'MA')
  - documents.state / district_name / school_year / quarter_month /
    meeting_doc_type / meeting_body / document_quality

Backfills schools.state='MA' for existing rows (the seeded corpus is all
Massachusetts districts) and denormalizes state/district_name onto existing
school_scraper documents from their source_metadata.

Revises: 9d23df92a0ce
Create Date: 2026-07-29 00:00:01
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260729_000001"
down_revision: Union[str, None] = "9d23df92a0ce"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── 1. schools.state ────────────────────────────────────────────────────
    op.add_column(
        "schools",
        sa.Column(
            "state",
            sa.String(length=2),
            nullable=False,
            server_default="MA",
        ),
    )
    op.create_index("ix_schools_state", "schools", ["state"])

    # ── 2. documents V1 heatmap columns ─────────────────────────────────────
    op.add_column(
        "documents", sa.Column("state", sa.String(length=2), nullable=True)
    )
    op.add_column(
        "documents",
        sa.Column("district_name", sa.String(length=512), nullable=True),
    )
    op.add_column(
        "documents",
        sa.Column("school_year", sa.String(length=9), nullable=True),
    )
    op.add_column(
        "documents",
        sa.Column("quarter_month", sa.String(length=7), nullable=True),
    )
    op.add_column(
        "documents",
        sa.Column("meeting_doc_type", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "documents",
        sa.Column("meeting_body", sa.String(length=128), nullable=True),
    )
    op.add_column(
        "documents",
        sa.Column(
            "document_quality",
            sa.String(length=32),
            nullable=False,
            server_default="clean_digital",
        ),
    )
    op.create_index("ix_documents_state", "documents", ["state"])
    op.create_index("ix_documents_district_name", "documents", ["district_name"])
    op.create_index("ix_documents_school_year", "documents", ["school_year"])
    op.create_index(
        "ix_documents_quarter_month", "documents", ["quarter_month"]
    )
    op.create_index(
        "ix_documents_meeting_doc_type", "documents", ["meeting_doc_type"]
    )
    op.create_index("ix_documents_meeting_body", "documents", ["meeting_body"])
    op.create_index(
        "ix_documents_document_quality", "documents", ["document_quality"]
    )

    # ── 3. Backfill schools.state='MA' for any pre-existing NULLs ───────────
    op.execute("UPDATE schools SET state = 'MA' WHERE state IS NULL OR state = ''")

    # ── 4. Denormalize state + district_name onto existing school_scraper docs
    # Source metadata shape (set in app/tasks/school_scraper_tasks.py):
    #   { school_name, school_org_code, doc_year, meeting_date, ... }
    # State for the existing corpus is always 'MA'.
    op.execute(
        """
        UPDATE documents d
        SET
            state = 'MA',
            district_name = COALESCE(
                d.district_name,
                (d.source_metadata->>'school_name')::text
            )
        WHERE d.source_type = 'school_scraper'
          AND (d.state IS NULL OR d.state = '')
        """
    )


def downgrade() -> None:
    op.drop_index("ix_documents_document_quality", table_name="documents")
    op.drop_index("ix_documents_meeting_body", table_name="documents")
    op.drop_index("ix_documents_meeting_doc_type", table_name="documents")
    op.drop_index("ix_documents_quarter_month", table_name="documents")
    op.drop_index("ix_documents_school_year", table_name="documents")
    op.drop_index("ix_documents_district_name", table_name="documents")
    op.drop_index("ix_documents_state", table_name="documents")
    op.drop_column("documents", "document_quality")
    op.drop_column("documents", "meeting_body")
    op.drop_column("documents", "meeting_doc_type")
    op.drop_column("documents", "quarter_month")
    op.drop_column("documents", "school_year")
    op.drop_column("documents", "district_name")
    op.drop_column("documents", "state")

    op.drop_index("ix_schools_state", table_name="schools")
    op.drop_column("schools", "state")
