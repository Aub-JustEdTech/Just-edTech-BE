"""widen_scraped_media_status_to_32

Widens scraped_media.status from VARCHAR(16) to VARCHAR(32).

At 16 characters the column overflows on values the ingest task already
writes — "skipped_duplicate" (17) — and on the statuses the transcription
work adds: "skipped_too_large" (17), "skipped_too_long" (16 + margin),
"media_unavailable" (17). The symptom is StringDataRightTruncation at the
end of an otherwise-successful ingest.

Widening a varchar in PostgreSQL is a catalog-only change (no table
rewrite, no full-table lock), so this is safe to apply to a live table.

NOTE: autogenerate additionally proposed dropping the LangGraph
`checkpoint*` tables and reshuffling several unrelated indexes. Those are
pre-existing drift from `Base.metadata.create_all()`, not part of this
change, and dropping the checkpoint tables would destroy conversation
state — so they were deliberately removed from this revision.

Revision ID: e40a3d4fec9c
Revises: 9d23df92a0ce
Create Date: 2026-07-29 17:52:02.057456

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e40a3d4fec9c"
down_revision: str | None = "9d23df92a0ce"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column(
        "scraped_media",
        "status",
        existing_type=sa.VARCHAR(length=16),
        type_=sa.String(length=32),
        existing_nullable=False,
    )


def downgrade() -> None:
    # Narrowing fails on any row already holding a longer value, so truncate
    # first. Without this the downgrade errors out on real data.
    op.execute(
        "UPDATE scraped_media SET status = LEFT(status, 16) "
        "WHERE LENGTH(status) > 16"
    )
    op.alter_column(
        "scraped_media",
        "status",
        existing_type=sa.String(length=32),
        type_=sa.VARCHAR(length=16),
        existing_nullable=False,
    )
