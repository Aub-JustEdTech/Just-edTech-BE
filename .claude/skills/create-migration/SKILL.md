---
name: create-migration
description: Creates and applies an Alembic database migration. Use when the user adds a model, changes a column, adds or removes a table, modifies a relationship, renames a field, or asks to create or run a migration.
allowed-tools: Bash, Read
---

When creating a database migration:

## 1. Make the Model Change First

Edit the SQLAlchemy model in `app/models/{domain}.py` before generating the migration. Alembic autogenerates by diffing models against the current DB state.

```python
# Example: adding a new column
class Document(BaseModel):
    __tablename__ = "documents"
    # ... existing columns ...
    summary = Column(Text, nullable=True)  # ← new column
```

## 2. Generate the Migration

```bash
alembic revision --autogenerate -m "short_description_of_change"
```

Description conventions (snake_case, concise):
- `add_summary_column_to_documents`
- `create_heatmap_tables`
- `add_index_on_tenant_id`
- `rename_chatbot_config_to_settings`

## 3. Review the Generated File

**Always read the generated file before applying.** Find it in `alembic/versions/`:

```bash
# List migration files to find the new one
ls -lt alembic/versions/
```

Check:
- `upgrade()` does what you expect (adds/modifies the right columns/tables)
- `downgrade()` correctly reverses the change — never leave it as `pass`
- No unintended table drops or column removals from autogenerate false positives
- Data types match your model definition

## 4. Apply the Migration

```bash
alembic upgrade head
```

## 5. Rollback if Needed

```bash
alembic downgrade -1    # roll back one migration
alembic downgrade base  # roll back everything (destructive — use with caution)
```

## 6. Useful Alembic Commands

```bash
alembic current           # show current applied migration
alembic history           # list all migrations in order
alembic show <rev_id>     # show details of a specific migration
```

## Constraints

- **Never modify an existing migration file** — always create a new one
- **Never write migration SQL by hand** — always use `--autogenerate` and review
- **Never create or drop tables directly in SQL** — Alembic is the only migration path
- Always implement `downgrade()` — a migration without a working downgrade is a one-way door
- Verify DB is running before generating or applying: `docker-compose up -d postgres`
