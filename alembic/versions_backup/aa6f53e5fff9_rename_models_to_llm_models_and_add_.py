"""rename_models_to_llm_models_and_add_tenant_id

Revision ID: aa6f53e5fff9
Revises: 0ccc8288ae0a
Create Date: 2025-10-06 12:37:53.300404

"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "aa6f53e5fff9"
down_revision: str | None = "20251006_161000"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1) Drop FKs in tenant_configs that reference 'models' to avoid dependency errors
    op.drop_constraint(
        op.f("tenant_configs_vec_db_id_fkey"), "tenant_configs", type_="foreignkey"
    )
    op.drop_constraint(
        op.f("tenant_configs_embedding_model_id_fkey"),
        "tenant_configs",
        type_="foreignkey",
    )
    op.drop_constraint(
        op.f("tenant_configs_chat_model_id_fkey"), "tenant_configs", type_="foreignkey"
    )

    # 2) Add tenant_id to existing 'models' table first (nullable for safe backfill)
    with op.batch_alter_table("models") as batch_op:
        batch_op.add_column(sa.Column("tenant_id", sa.BigInteger(), nullable=True))
        batch_op.create_foreign_key(
            None, "tenants", ["tenant_id"], ["id"], ondelete="CASCADE"
        )

    # Optional: backfill tenant_id if needed (set to Default Tenant as in initial migration)
    bind = op.get_bind()
    tenant_id = bind.execute(
        sa.text(
            """
        INSERT INTO tenants (name, domain, created_at, updated_at)
        VALUES (:name, :domain, NOW(), NOW())
        ON CONFLICT (domain) DO NOTHING
        RETURNING id
        """
        ),
        {"name": "Default Tenant", "domain": "default.local"},
    ).scalar()
    if tenant_id is None:
        tenant_id = bind.execute(
            sa.text("SELECT id FROM tenants WHERE domain = :domain"),
            {"domain": "default.local"},
        ).scalar()
    # Set tenant_id for existing rows if any
    bind.execute(
        sa.text("UPDATE models SET tenant_id = :tid WHERE tenant_id IS NULL"),
        {"tid": tenant_id},
    )

    # Make tenant_id NOT NULL
    with op.batch_alter_table("models") as batch_op:
        batch_op.alter_column(
            "tenant_id", existing_type=sa.BigInteger(), nullable=False
        )

    # 3) Rename table 'models' -> 'llm_models' (preserves data)
    op.rename_table("models", "llm_models")

    # 4) Rename index if it exists
    try:
        op.execute("ALTER INDEX IF EXISTS ix_models_id RENAME TO ix_llm_models_id")
    except Exception:
        pass

    # 5) Recreate FKs in tenant_configs pointing to new table
    op.create_foreign_key(None, "tenant_configs", "llm_models", ["vec_db_id"], ["id"])
    op.create_foreign_key(
        None, "tenant_configs", "llm_models", ["chat_model_id"], ["id"]
    )
    op.create_foreign_key(
        None, "tenant_configs", "llm_models", ["embedding_model_id"], ["id"]
    )

    # Note: Unique constraint on conversation_documents already exists from base migration; do not recreate here


def downgrade() -> None:
    # Drop FKs to llm_models first
    op.drop_constraint(None, "tenant_configs", type_="foreignkey")
    op.drop_constraint(None, "tenant_configs", type_="foreignkey")
    op.drop_constraint(None, "tenant_configs", type_="foreignkey")

    # Rename index back if present
    try:
        op.execute("ALTER INDEX IF EXISTS ix_llm_models_id RENAME TO ix_models_id")
    except Exception:
        pass

    # Rename table back
    op.rename_table("llm_models", "models")

    # Drop tenant_id FK and column
    with op.batch_alter_table("models") as batch_op:
        batch_op.drop_constraint(None, type_="foreignkey")
        batch_op.drop_column("tenant_id")

    # Recreate FKs in tenant_configs back to models
    op.create_foreign_key(
        op.f("tenant_configs_chat_model_id_fkey"),
        "tenant_configs",
        "models",
        ["chat_model_id"],
        ["id"],
    )
    op.create_foreign_key(
        op.f("tenant_configs_embedding_model_id_fkey"),
        "tenant_configs",
        "models",
        ["embedding_model_id"],
        ["id"],
    )
    op.create_foreign_key(
        op.f("tenant_configs_vec_db_id_fkey"),
        "tenant_configs",
        "models",
        ["vec_db_id"],
        ["id"],
    )
