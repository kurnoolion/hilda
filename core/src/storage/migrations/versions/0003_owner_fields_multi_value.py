"""OWNER-1 (2026-08-14) -- add JSON list columns for the 4 owner identity
fields, backfilled from existing singular columns.

Revision ID: 0003_owner_fields_multi_value
Revises:    0002_document_version

Per architect 2026-08-14: multi-owner items (co-owners, backup contacts)
need list-valued owner_* fields. Option B additive migration adds 4 new
`_list` JSON columns alongside the existing singular columns, backfilled
so `owner_corp_email_list = [owner_corp_email]` when singular is non-empty,
`[]` when NULL or empty.

Existing singular columns are LEFT IN PLACE during OWNER-2..6. Ingest
paths dual-write (populate both). Reader migration is incremental --
outreach (OWNER-3), owner_reply (OWNER-4), plm_poll (OWNER-5), and
display paths (OWNER-6) each migrate one at a time to read from the
`_list` columns. Final OWNER-7 migration drops the singular columns
and renames `_list` → singular per B-final-B.

No SP UI engineer coordination needed. TPMs continue to edit the same
SP text column with semicolon-separated values ("alice@corp; bob@corp;").
HILDA's _split_owner_list helper (OWNER-2) parses it at ingest.

Postgres-side migration only. Tests use Base.metadata.create_all() from
storage/db.py, so the new columns land in test DBs via the updated
DeliveryItemTable definition without needing this Alembic to run.
"""
from alembic import op
import sqlalchemy as sa


revision = "0003_owner_fields_multi_value"
down_revision = "0002_document_version"
branch_labels = None
depends_on = None


# The 4 owner identity fields being extended per OWNER-1.
# New column name = <singular>_list; type = JSON list, default = [].
_OWNER_FIELDS = (
    "owner_name",
    "owner_corp_email",
    "owner_corp_usa_email",
    "owner_corp_id",
)


def upgrade() -> None:
    """Add 4 new JSON list columns + backfill from existing singular columns."""
    for singular in _OWNER_FIELDS:
        list_col = f"{singular}_list"
        # ADD COLUMN with default [] so existing rows land with a valid list
        # from the outset. NOT NULL enforced so readers never need to null-check.
        op.add_column(
            "delivery_item",
            sa.Column(
                list_col,
                sa.JSON(),
                nullable=False,
                server_default="[]",
            ),
        )
        # Backfill: wrap singular into single-element list when non-empty.
        # PostgreSQL-flavored jsonb_build_array; run only against PG (this
        # migration file never executes against SQLite -- tests use
        # metadata.create_all() with the JSON default from db.py instead).
        op.execute(
            f"""
            UPDATE delivery_item
               SET {list_col} = CASE
                   WHEN {singular} IS NULL OR {singular} = '' THEN '[]'::jsonb
                   ELSE jsonb_build_array({singular})
               END
            """
        )


def downgrade() -> None:
    """Drop the 4 new JSON list columns. Singular columns untouched
    (they were never removed on upgrade)."""
    for singular in _OWNER_FIELDS:
        op.drop_column("delivery_item", f"{singular}_list")
