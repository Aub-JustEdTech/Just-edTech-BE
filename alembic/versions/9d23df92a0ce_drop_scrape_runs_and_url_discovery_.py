"""drop_scrape_runs_and_url_discovery_tables

Revision ID: 9d23df92a0ce
Revises: 20260727_000001
Create Date: 2026-07-27 10:58:30.726320

Removes scrape run/job tracking and stored URL-discovery tables.
Keeps school_scrape_urls (confirmed scrape URLs) and scraped_media.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "9d23df92a0ce"
down_revision: Union[str, None] = "20260727_000001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # scraped_media lineage columns (nullable FKs → dropped tables)
    op.drop_constraint(
        "scraped_media_scrape_job_id_fkey", "scraped_media", type_="foreignkey"
    )
    op.drop_constraint(
        "scraped_media_scrape_run_id_fkey", "scraped_media", type_="foreignkey"
    )
    op.drop_column("scraped_media", "scrape_job_id")
    op.drop_column("scraped_media", "scrape_run_id")

    # school_scrape_jobs before scrape_runs (FK dependency)
    op.drop_index("ix_school_scrape_jobs_status", table_name="school_scrape_jobs")
    op.drop_index(
        "ix_school_scrape_jobs_scrape_url_id", table_name="school_scrape_jobs"
    )
    op.drop_index("ix_school_scrape_jobs_school_id", table_name="school_scrape_jobs")
    op.drop_index("ix_school_scrape_jobs_run_id", table_name="school_scrape_jobs")
    op.drop_index("ix_school_scrape_jobs_id", table_name="school_scrape_jobs")
    op.drop_table("school_scrape_jobs")

    op.drop_index("ix_scrape_runs_status", table_name="scrape_runs")
    op.drop_index("ix_scrape_runs_tenant_id", table_name="scrape_runs")
    op.drop_index("ix_scrape_runs_id", table_name="scrape_runs")
    op.drop_table("scrape_runs")

    # school_url_candidates before school_url_discoveries (FK dependency)
    op.drop_index(
        "ix_school_url_candidates_school_rank", table_name="school_url_candidates"
    )
    op.drop_index(
        "ix_school_url_candidates_discovery_id", table_name="school_url_candidates"
    )
    op.drop_index("ix_school_url_candidates_school_id", table_name="school_url_candidates")
    op.drop_index("ix_school_url_candidates_id", table_name="school_url_candidates")
    op.drop_table("school_url_candidates")

    op.drop_index(
        "ix_school_url_discoveries_tenant_id", table_name="school_url_discoveries"
    )
    op.drop_index(
        "ix_school_url_discoveries_school_id", table_name="school_url_discoveries"
    )
    op.drop_index("ix_school_url_discoveries_id", table_name="school_url_discoveries")
    op.drop_table("school_url_discoveries")


def downgrade() -> None:
    op.create_table(
        "school_url_discoveries",
        sa.Column("school_id", sa.BIGINT(), nullable=False),
        sa.Column("tenant_id", sa.BIGINT(), nullable=False),
        sa.Column("discovery_method", sa.VARCHAR(length=32), nullable=True),
        sa.Column("total_urls_scanned", sa.INTEGER(), nullable=False),
        sa.Column("error", sa.TEXT(), nullable=True),
        sa.Column("id", sa.BIGINT(), autoincrement=True, nullable=False),
        sa.Column("created_at", postgresql.TIMESTAMP(), nullable=False),
        sa.Column("updated_at", postgresql.TIMESTAMP(), nullable=False),
        sa.ForeignKeyConstraint(
            ["school_id"], ["schools.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("school_id", name="uq_school_url_discovery_school"),
    )
    op.create_index(
        "ix_school_url_discoveries_id",
        "school_url_discoveries",
        ["id"],
        unique=False,
    )
    op.create_index(
        "ix_school_url_discoveries_school_id",
        "school_url_discoveries",
        ["school_id"],
        unique=False,
    )
    op.create_index(
        "ix_school_url_discoveries_tenant_id",
        "school_url_discoveries",
        ["tenant_id"],
        unique=False,
    )

    op.create_table(
        "school_url_candidates",
        sa.Column("school_id", sa.BIGINT(), nullable=False),
        sa.Column("tenant_id", sa.BIGINT(), nullable=False),
        sa.Column("discovery_id", sa.BIGINT(), nullable=False),
        sa.Column("url", sa.TEXT(), nullable=False),
        sa.Column("url_hash", sa.VARCHAR(length=64), nullable=False),
        sa.Column(
            "matched_keywords",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("score", sa.SMALLINT(), nullable=False),
        sa.Column("rank", sa.SMALLINT(), nullable=False),
        sa.Column("id", sa.BIGINT(), autoincrement=True, nullable=False),
        sa.Column("created_at", postgresql.TIMESTAMP(), nullable=False),
        sa.Column("updated_at", postgresql.TIMESTAMP(), nullable=False),
        sa.Column("data_type", sa.VARCHAR(length=32), nullable=True),
        sa.Column(
            "is_archive",
            sa.BOOLEAN(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column(
            "data_years_available",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["discovery_id"],
            ["school_url_discoveries.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["school_id"], ["schools.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "school_id", "url_hash", name="uq_school_url_candidate_school_hash"
        ),
    )
    op.create_index(
        "ix_school_url_candidates_id", "school_url_candidates", ["id"], unique=False
    )
    op.create_index(
        "ix_school_url_candidates_school_id",
        "school_url_candidates",
        ["school_id"],
        unique=False,
    )
    op.create_index(
        "ix_school_url_candidates_discovery_id",
        "school_url_candidates",
        ["discovery_id"],
        unique=False,
    )
    op.create_index(
        "ix_school_url_candidates_school_rank",
        "school_url_candidates",
        ["school_id", "rank"],
        unique=False,
    )

    op.create_table(
        "scrape_runs",
        sa.Column("id", sa.BIGINT(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.BIGINT(), nullable=False),
        sa.Column("triggered_by", sa.VARCHAR(length=32), nullable=False),
        sa.Column("status", sa.VARCHAR(length=16), nullable=False),
        sa.Column("started_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("completed_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("total_schools", sa.INTEGER(), nullable=False),
        sa.Column("schools_completed", sa.INTEGER(), nullable=False),
        sa.Column("schools_failed", sa.INTEGER(), nullable=False),
        sa.Column("schools_skipped", sa.INTEGER(), nullable=False),
        sa.Column("media_found", sa.INTEGER(), nullable=False),
        sa.Column("media_new", sa.INTEGER(), nullable=False),
        sa.Column("media_skipped_duplicate", sa.INTEGER(), nullable=False),
        sa.Column(
            "error_summary", postgresql.JSONB(astext_type=sa.Text()), nullable=True
        ),
        sa.Column("created_at", postgresql.TIMESTAMP(), nullable=False),
        sa.Column("updated_at", postgresql.TIMESTAMP(), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_scrape_runs_id", "scrape_runs", ["id"], unique=False)
    op.create_index("ix_scrape_runs_tenant_id", "scrape_runs", ["tenant_id"], unique=False)
    op.create_index("ix_scrape_runs_status", "scrape_runs", ["status"], unique=False)

    op.create_table(
        "school_scrape_jobs",
        sa.Column("id", sa.BIGINT(), autoincrement=True, nullable=False),
        sa.Column("run_id", sa.BIGINT(), nullable=False),
        sa.Column("school_id", sa.BIGINT(), nullable=False),
        sa.Column("scrape_url_id", sa.BIGINT(), nullable=False),
        sa.Column("status", sa.VARCHAR(length=16), nullable=False),
        sa.Column("started_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("completed_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("pages_crawled", sa.INTEGER(), nullable=False),
        sa.Column("media_found", sa.INTEGER(), nullable=False),
        sa.Column("media_new", sa.INTEGER(), nullable=False),
        sa.Column("media_skipped_duplicate", sa.INTEGER(), nullable=False),
        sa.Column("error_message", sa.TEXT(), nullable=True),
        sa.Column(
            "scrape_result", postgresql.JSONB(astext_type=sa.Text()), nullable=True
        ),
        sa.Column("created_at", postgresql.TIMESTAMP(), nullable=False),
        sa.Column("updated_at", postgresql.TIMESTAMP(), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["scrape_runs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["school_id"], ["schools.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["scrape_url_id"], ["school_scrape_urls.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_school_scrape_jobs_id", "school_scrape_jobs", ["id"], unique=False
    )
    op.create_index(
        "ix_school_scrape_jobs_run_id", "school_scrape_jobs", ["run_id"], unique=False
    )
    op.create_index(
        "ix_school_scrape_jobs_school_id",
        "school_scrape_jobs",
        ["school_id"],
        unique=False,
    )
    op.create_index(
        "ix_school_scrape_jobs_scrape_url_id",
        "school_scrape_jobs",
        ["scrape_url_id"],
        unique=False,
    )
    op.create_index(
        "ix_school_scrape_jobs_status", "school_scrape_jobs", ["status"], unique=False
    )

    op.add_column(
        "scraped_media",
        sa.Column("scrape_run_id", sa.BIGINT(), autoincrement=False, nullable=True),
    )
    op.add_column(
        "scraped_media",
        sa.Column("scrape_job_id", sa.BIGINT(), autoincrement=False, nullable=True),
    )
    op.create_foreign_key(
        "scraped_media_scrape_run_id_fkey",
        "scraped_media",
        "scrape_runs",
        ["scrape_run_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "scraped_media_scrape_job_id_fkey",
        "scraped_media",
        "school_scrape_jobs",
        ["scrape_job_id"],
        ["id"],
        ondelete="SET NULL",
    )
