"""OWNER-7 (2026-08-16) -- B-final-B cleanup: drop the 4 singular owner
identity columns and rename `owner_*_list` back to `owner_*` (unsuffixed)
so the final schema uses the same names as pre-OWNER-1, just list-typed.

Revision ID: 0004_owner_fields_b_final_b
Revises:    0003_owner_fields_multi_value

Per architect 2026-08-14 (B-final-B). Concludes the OWNER-1..7 additive
migration cascade per [D-172]. After this migration, `delivery_item` has:
  * NO `owner_*` String columns (dropped this migration)
  * NO `owner_*_list` JSON columns (renamed to `owner_*`)
  * 4 `owner_*` JSON list columns (renamed from `_list`), NOT NULL, default [].

Pre-req: all readers must consume the list form BEFORE this migration
runs. OWNER-2 (ingest) / OWNER-3 (outreach) / OWNER-4 (owner_reply) /
OWNER-5 (plm_poll) / OWNER-6 (DRR display) have all migrated per prior
close-session commits. This migration is the final flip.

DocumentItemAssociationTable owner_* fields intentionally UNCHANGED --
they remain singular String snapshots per OWNER-1 (historic-snapshot
semantics + `ix_assoc_owner_corp_id` index + FR-5/D-035 PLM fan-out).

Postgres-side migration only. Tests use Base.metadata.create_all() from
storage/db.py, so the renamed columns land in test DBs via the updated
DeliveryItemTable definition without needing this Alembic to run.
"""
from alembic import op
import sqlalchemy as sa


revision = "0004_owner_fields_b_final_b"
down_revision = "0003_owner_fields_multi_value"
branch_labels = None
depends_on = None


_OWNER_FIELDS = (
    "owner_name",
    "owner_corp_email",
    "owner_corp_usa_email",
    "owner_corp_id",
)


def upgrade() -> None:
    """Drop singular String columns; rename `_list` JSON columns to
    unsuffixed. Order matters: drop the singular FIRST (frees the name),
    then rename `_list` INTO that name."""
    for singular in _OWNER_FIELDS:
        op.drop_column("delivery_item", singular)
    for singular in _OWNER_FIELDS:
        op.alter_column(
            "delivery_item",
            f"{singular}_list",
            new_column_name=singular,
        )


def downgrade() -> None:
    """Reverse: rename `owner_*` (list) back to `owner_*_list`, re-add the
    singular String columns as nullable (backfill from first list entry --
    lossy for multi-owner rows but only informative pre-OWNER-1 anyway)."""
    for singular in _OWNER_FIELDS:
        op.alter_column(
            "delivery_item",
            singular,
            new_column_name=f"{singular}_list",
        )
    for singular in _OWNER_FIELDS:
        length = 128 if singular == "owner_corp_id" else 256
        op.add_column(
            "delivery_item",
            sa.Column(singular, sa.String(length), nullable=True),
        )
        # Best-effort backfill from first list entry (jsonb ->> 0). Lossy for
        # multi-owner rows but the singular field never supported that anyway.
        op.execute(
            f"""
            UPDATE delivery_item
               SET {singular} = ({singular}_list::jsonb ->> 0)
             WHERE jsonb_array_length({singular}_list::jsonb) > 0
            """
        )
