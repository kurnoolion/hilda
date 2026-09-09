"""UPLOAD-FOLDER-OVERRIDE-1 (2026-09-06) -- per-document carrier folder.

Revision ID: 0005_upload_folder_override
Revises:    0004_owner_fields_b_final_b

The revision id is NOT `0005_upload_target_folder_override` (the column's own
name): at 34 characters that overflows `alembic_version.version_num`, which
alembic creates as VARCHAR(32). The upgrade fails at the version bookkeeping
with StringDataRightTruncationError and the whole transaction rolls back, so
the column never lands. Keep revision ids at 32 characters or fewer --
widening the column instead would be undone the next time alembic creates
`alembic_version` on a fresh database.

Adds one nullable column to `document_item_association`:

    upload_target_folder_override  VARCHAR(512) NULL

When set, submit_to_carrier uses it in place of the work item's
`target_folder`; the archive-derived subdir still rides underneath, so zip
structure is preserved. NULL -- the overwhelming majority of rows -- means
"use the item's folder", so this migration is a pure additive no-op for
existing data and needs no backfill.

Why an override rather than re-assigning the document to a different work
item: a TPM's concern is WHERE a document lands on Google Drive, not which
item holds it, and a folder is frequently owned by several items (P1 #14 and
#20 share one) -- so a folder choice carries no information about which item
was meant. Re-assignment would additionally have required splitting the
revision family, moving internal-tree files, changing which P1 item a DRR
document migrates to, and relaxing the STR-E009 ordering guard. The override
changes the destination and nothing else.

Postgres-side only. Tests build schema from Base.metadata.create_all() in
storage/db.py, so the column lands in test DBs via the updated
DocumentItemAssociationTable definition without this migration running.
"""
from alembic import op
import sqlalchemy as sa


revision = "0005_upload_folder_override"
down_revision = "0004_owner_fields_b_final_b"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "document_item_association",
        sa.Column("upload_target_folder_override", sa.String(512), nullable=True),
    )


def downgrade() -> None:
    """Drops the column. Any overrides a TPM had set are lost and those
    documents revert to their work item's target_folder -- acceptable, since
    that is exactly where they went before the feature existed."""
    op.drop_column("document_item_association", "upload_target_folder_override")
