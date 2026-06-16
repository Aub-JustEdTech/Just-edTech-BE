"""add_document_intelligence_columns

Revision ID: 20260306_000001
Revises: 20260112_153917
Create Date: 2026-03-06 00:00:01

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

# revision identifiers, used by Alembic.
revision: str = "20260306_000001"
down_revision: Union[str, None] = "1ff7c73a4a52"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("documents", sa.Column("summary", sa.Text(), nullable=True))
    op.add_column(
        "documents", sa.Column("doc_category", sa.String(100), nullable=True)
    )
    op.add_column(
        "documents", sa.Column("doc_date_range", sa.String(100), nullable=True)
    )
    op.add_column(
        "documents", sa.Column("source_id", sa.String(255), nullable=True)
    )
    op.add_column(
        "documents", sa.Column("source_type", sa.String(50), nullable=True)
    )
    op.add_column(
        "documents", sa.Column("source_metadata", JSONB(), nullable=True)
    )

    op.create_index(
        "ix_documents_source_id", "documents", ["source_id"], unique=False
    )
    op.create_index(
        "ix_documents_source_type", "documents", ["source_type"], unique=False
    )
    op.create_index(
        "ix_documents_doc_category", "documents", ["doc_category"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_documents_doc_category", table_name="documents")
    op.drop_index("ix_documents_source_type", table_name="documents")
    op.drop_index("ix_documents_source_id", table_name="documents")

    op.drop_column("documents", "source_metadata")
    op.drop_column("documents", "source_type")
    op.drop_column("documents", "source_id")
    op.drop_column("documents", "doc_date_range")
    op.drop_column("documents", "doc_category")
    op.drop_column("documents", "summary")
