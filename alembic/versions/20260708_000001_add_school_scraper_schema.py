"""add_school_scraper_schema

Creates the schools, school_scrape_urls, scrape_runs, school_scrape_jobs,
and scraped_media tables backing the district-level school scraping
knowledge base.

Revision ID: 20260708_000001
Revises: 20260703_000001
Create Date: 2026-07-08 00:00:01

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "20260708_000001"
down_revision: Union[str, None] = "20260703_000001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # school_scrape_urls is created BEFORE schools because schools has a
    # self-referential FK back to school_scrape_urls.scrape_url_id
    # (the school's "primary" confirmed URL). We allow the schools->urls
    # FK to be deferred so the cycle resolves cleanly.
    op.create_table(
        "school_scrape_urls",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("school_id", sa.BigInteger(), nullable=True),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("crawl_depth", sa.SmallInteger(), nullable=False),
        sa.Column("use_playwright", sa.Boolean(), nullable=False),
        sa.Column("confirmed_by_user_id", sa.BigInteger(), nullable=True),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_http_status", sa.Integer(), nullable=True),
        sa.Column("last_crawl_page_count", sa.Integer(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["confirmed_by_user_id"], ["users.id"], ondelete="SET NULL"
        ),
        # school_id FK added below after schools exists; declared nullable
        # here so the cycle resolves. We re-add the proper CASCADE FK after.
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "school_id", "url", name="uq_scrape_url_school_url"
        ),
    )
    op.create_index(
        op.f("ix_school_scrape_urls_id"),
        "school_scrape_urls",
        ["id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_school_scrape_urls_school_id"),
        "school_scrape_urls",
        ["school_id"],
        unique=False,
    )

    # schools
    op.create_table(
        "schools",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("tenant_id", sa.BigInteger(), nullable=False),
        sa.Column("org_code", sa.String(length=16), nullable=False),
        sa.Column("name", sa.String(length=512), nullable=False),
        sa.Column("district_type", sa.String(length=64), nullable=False),
        sa.Column("website", sa.Text(), nullable=True),
        sa.Column("last_scrapped_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("scrape_url_id", sa.BigInteger(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["scrape_url_id"], ["school_scrape_urls.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id", "org_code", name="uq_schools_tenant_org"
        ),
    )
    op.create_index(
        op.f("ix_schools_id"), "schools", ["id"], unique=False
    )
    op.create_index(
        op.f("ix_schools_tenant_id"), "schools", ["tenant_id"], unique=False
    )
    op.create_index(
        op.f("ix_schools_org_code"), "schools", ["org_code"], unique=False
    )

    # Now add the proper CASCADE FK from school_scrape_urls.school_id
    # -> schools.id (deferred because of the cycle).
    op.create_foreign_key(
        "fk_school_scrape_urls_school_id_schools",
        "school_scrape_urls",
        "schools",
        ["school_id"],
        ["id"],
        ondelete="CASCADE",
    )

    # scrape_runs
    op.create_table(
        "scrape_runs",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("tenant_id", sa.BigInteger(), nullable=False),
        sa.Column("triggered_by", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("total_schools", sa.Integer(), nullable=False),
        sa.Column("schools_completed", sa.Integer(), nullable=False),
        sa.Column("schools_failed", sa.Integer(), nullable=False),
        sa.Column("schools_skipped", sa.Integer(), nullable=False),
        sa.Column("media_found", sa.Integer(), nullable=False),
        sa.Column("media_new", sa.Integer(), nullable=False),
        sa.Column("media_skipped_duplicate", sa.Integer(), nullable=False),
        sa.Column("error_summary", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_scrape_runs_id"), "scrape_runs", ["id"], unique=False
    )
    op.create_index(
        op.f("ix_scrape_runs_tenant_id"), "scrape_runs", ["tenant_id"], unique=False
    )
    op.create_index(
        op.f("ix_scrape_runs_status"), "scrape_runs", ["status"], unique=False
    )

    # school_scrape_jobs
    op.create_table(
        "school_scrape_jobs",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("run_id", sa.BigInteger(), nullable=False),
        sa.Column("school_id", sa.BigInteger(), nullable=False),
        sa.Column("scrape_url_id", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("pages_crawled", sa.Integer(), nullable=False),
        sa.Column("media_found", sa.Integer(), nullable=False),
        sa.Column("media_new", sa.Integer(), nullable=False),
        sa.Column("media_skipped_duplicate", sa.Integer(), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("scrape_result", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["scrape_runs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["school_id"], ["schools.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["scrape_url_id"], ["school_scrape_urls.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_school_scrape_jobs_id"),
        "school_scrape_jobs",
        ["id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_school_scrape_jobs_run_id"),
        "school_scrape_jobs",
        ["run_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_school_scrape_jobs_school_id"),
        "school_scrape_jobs",
        ["school_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_school_scrape_jobs_scrape_url_id"),
        "school_scrape_jobs",
        ["scrape_url_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_school_scrape_jobs_status"),
        "school_scrape_jobs",
        ["status"],
        unique=False,
    )

    # scraped_media
    op.create_table(
        "scraped_media",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("tenant_id", sa.BigInteger(), nullable=False),
        sa.Column("school_id", sa.BigInteger(), nullable=False),
        sa.Column("school_org_code", sa.String(length=16), nullable=False),
        sa.Column("school_name", sa.String(length=512), nullable=True),
        sa.Column("district_type", sa.String(length=64), nullable=True),
        sa.Column("scrape_job_id", sa.BigInteger(), nullable=True),
        sa.Column("scrape_run_id", sa.BigInteger(), nullable=True),
        sa.Column("source_page_url", sa.Text(), nullable=False),
        sa.Column("source_media_url", sa.Text(), nullable=False),
        sa.Column("url_hash", sa.String(length=64), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=True),
        sa.Column("media_type", sa.String(length=16), nullable=False),
        sa.Column("file_extension", sa.String(length=16), nullable=True),
        sa.Column("original_name", sa.Text(), nullable=True),
        sa.Column("document_type", sa.String(length=32), nullable=True),
        sa.Column("meeting_date", sa.Date(), nullable=True),
        sa.Column("s3_key_raw", sa.Text(), nullable=True),
        sa.Column("s3_key_text", sa.Text(), nullable=True),
        sa.Column("size_bytes", sa.BigInteger(), nullable=True),
        sa.Column("duration_seconds", sa.Integer(), nullable=True),
        sa.Column("document_id", sa.BigInteger(), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("scraped_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ingested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["document_id"], ["documents.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["scrape_job_id"], ["school_scrape_jobs.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["scrape_run_id"], ["scrape_runs.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(["school_id"], ["schools.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "school_id",
            "content_hash",
            name="uq_scraped_media_school_content",
        ),
    )
    op.create_index(
        op.f("ix_scraped_media_id"), "scraped_media", ["id"], unique=False
    )
    op.create_index(
        op.f("ix_scraped_media_tenant_id"),
        "scraped_media",
        ["tenant_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_scraped_media_school_id"),
        "scraped_media",
        ["school_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_scraped_media_school_org_code"),
        "scraped_media",
        ["school_org_code"],
        unique=False,
    )
    op.create_index(
        op.f("ix_scraped_media_url_hash"),
        "scraped_media",
        ["url_hash"],
        unique=False,
    )
    op.create_index(
        op.f("ix_scraped_media_content_hash"),
        "scraped_media",
        ["content_hash"],
        unique=False,
    )
    op.create_index(
        op.f("ix_scraped_media_document_id"),
        "scraped_media",
        ["document_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_scraped_media_status"),
        "scraped_media",
        ["status"],
        unique=False,
    )
    op.create_index(
        "ix_scraped_media_tenant_org",
        "scraped_media",
        ["tenant_id", "school_org_code"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_scraped_media_tenant_org", table_name="scraped_media")
    op.drop_index(op.f("ix_scraped_media_status"), table_name="scraped_media")
    op.drop_index(op.f("ix_scraped_media_document_id"), table_name="scraped_media")
    op.drop_index(op.f("ix_scraped_media_content_hash"), table_name="scraped_media")
    op.drop_index(op.f("ix_scraped_media_url_hash"), table_name="scraped_media")
    op.drop_index(
        op.f("ix_scraped_media_school_org_code"), table_name="scraped_media"
    )
    op.drop_index(op.f("ix_scraped_media_school_id"), table_name="scraped_media")
    op.drop_index(op.f("ix_scraped_media_tenant_id"), table_name="scraped_media")
    op.drop_index(op.f("ix_scraped_media_id"), table_name="scraped_media")
    op.drop_table("scraped_media")

    op.drop_index(op.f("ix_school_scrape_jobs_status"), table_name="school_scrape_jobs")
    op.drop_index(
        op.f("ix_school_scrape_jobs_scrape_url_id"), table_name="school_scrape_jobs"
    )
    op.drop_index(
        op.f("ix_school_scrape_jobs_school_id"), table_name="school_scrape_jobs"
    )
    op.drop_index(op.f("ix_school_scrape_jobs_run_id"), table_name="school_scrape_jobs")
    op.drop_index(op.f("ix_school_scrape_jobs_id"), table_name="school_scrape_jobs")
    op.drop_table("school_scrape_jobs")

    op.drop_index(op.f("ix_scrape_runs_status"), table_name="scrape_runs")
    op.drop_index(op.f("ix_scrape_runs_tenant_id"), table_name="scrape_runs")
    op.drop_index(op.f("ix_scrape_runs_id"), table_name="scrape_runs")
    op.drop_table("scrape_runs")

    op.drop_index(op.f("ix_schools_org_code"), table_name="schools")
    op.drop_index(op.f("ix_schools_tenant_id"), table_name="schools")
    op.drop_index(op.f("ix_schools_id"), table_name="schools")
    op.drop_table("schools")

    op.drop_constraint(
        "fk_school_scrape_urls_school_id_schools",
        "school_scrape_urls",
        type_="foreignkey",
    )
    op.drop_index(
        op.f("ix_school_scrape_urls_school_id"), table_name="school_scrape_urls"
    )
    op.drop_index(op.f("ix_school_scrape_urls_id"), table_name="school_scrape_urls")
    op.drop_table("school_scrape_urls")
