"""drop_schools_scrape_url_id

Remove the single "primary" scrape URL pointer from the schools table.
A school can have many active SchoolScrapeUrl rows; the primary-URL
concept is no longer used — the scrape runner now iterates every active
URL per school.

Revision ID: 20260813_000001
Revises: ae2cbb605267
Create Date: 2026-08-13 00:00:01

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "20260813_000001"
down_revision: str | None = "ae2cbb605267"
branch_labels: str | None = None
depends_on: list[str] | None = None


def upgrade() -> None:
    op.drop_constraint(
        "schools_scrape_url_id_fkey",
        "schools",
        type_="foreignkey",
    )
    op.drop_column("schools", "scrape_url_id")


def downgrade() -> None:
    op.add_column(
        "schools",
        sa.Column(
            "scrape_url_id",
            sa.BigInteger(),
            nullable=True,
        ),
    )
    op.create_foreign_key(
        "schools_scrape_url_id_fkey",
        "schools",
        "school_scrape_urls",
        ["scrape_url_id"],
        ["id"],
        ondelete="SET NULL",
    )
