"""add_skipped_to_processingstatus_and_jobstatus

Adds ``'skipped'`` to the ``processingstatus`` and ``jobstatus`` Postgres
enums so the post-classification year gate can set
``ProcessingStatus.SKIPPED`` (distinct from ``FAILED`` -- the document was
intentionally excluded, not broken).

Revision ID: 36a93d1cb72e
Revises: e555c8a175ee
Create Date: 2026-08-24 12:02:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '36a93d1cb72e'
down_revision: Union[str, None] = 'e555c8a175ee'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TYPE processingstatus ADD VALUE IF NOT EXISTS 'skipped'")
    op.execute("ALTER TYPE jobstatus ADD VALUE IF NOT EXISTS 'skipped'")


def downgrade() -> None:
    # Postgres has no ALTER TYPE ... DROP VALUE; recreate each enum without
    # 'skipped'. Fails if any row still holds status='skipped' -- reassign
    # those rows before downgrading.
    op.execute("ALTER TABLE documents ALTER COLUMN processing_status TYPE VARCHAR(20)")
    op.execute("DROP TYPE processingstatus")
    op.execute(
        "CREATE TYPE processingstatus AS ENUM "
        "('pending', 'processing', 'completed', 'failed')"
    )
    op.execute(
        "ALTER TABLE documents ALTER COLUMN processing_status "
        "TYPE processingstatus USING processing_status::processingstatus"
    )

    op.execute("ALTER TABLE document_processing_jobs ALTER COLUMN status TYPE VARCHAR(20)")
    op.execute("DROP TYPE jobstatus")
    op.execute(
        "CREATE TYPE jobstatus AS ENUM "
        "('pending', 'processing', 'completed', 'failed')"
    )
    op.execute(
        "ALTER TABLE document_processing_jobs ALTER COLUMN status "
        "TYPE jobstatus USING status::jobstatus"
    )
