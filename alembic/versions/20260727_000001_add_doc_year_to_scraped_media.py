"""add_doc_year_to_scraped_media

Adds an additive `doc_year` column (nullable SMALLINT) to `scraped_media`
so the download-time year filter (SCHOOL_SCRAPER_ALLOWED_YEARS) can record
the inferred year even for status="skipped_year" rows. This makes coverage
audits ("how many 2023 docs were skipped across all districts?") a simple
indexed query instead of a URL re-parse.

Additive — existing scrape/ingest rows get NULL and are unaffected. The
existing `status` String column already accepts the new "skipped_year"
value with no DDL change.

Revision ID: 20260727_000001
Revises: 20260715_000001
Create Date: 2026-07-27 00:00:01
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260727_000001"
down_revision: Union[str, None] = "20260715_000001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "scraped_media",
        sa.Column("doc_year", sa.SmallInteger(), nullable=True),
    )
    op.create_index(
        "ix_scraped_media_doc_year",
        "scraped_media",
        ["doc_year"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_scraped_media_doc_year", table_name="scraped_media")
    op.drop_column("scraped_media", "doc_year")
