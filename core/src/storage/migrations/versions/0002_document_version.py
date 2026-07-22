"""document_version table for Ph-1 HILDA-side documents view per D-150.

Revision ID: 0002_document_version
Revises: 0001_baseline

One row per save event in the view tree (NSD `view/<c>/<d>/<m>/<tg>/...`).
Ph-1 keeps version history but has NO restore-resolution logic — that's Ph-2.
"""
from alembic import op
import sqlalchemy as sa


revision = "0002_document_version"
down_revision = "0001_baseline"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "document_version",
        sa.Column("version_id", sa.String(64), primary_key=True),
        sa.Column("view_relative_path", sa.String(1024), nullable=False),
        sa.Column("customer_id", sa.String(64), nullable=False),
        sa.Column("device_id", sa.String(64), nullable=False),
        sa.Column("milestone_id", sa.String(64), nullable=False),
        sa.Column("tg_name", sa.String(128), nullable=False),
        sa.Column("filename", sa.String(512), nullable=False),
        sa.Column("version_num", sa.Integer, nullable=False),
        sa.Column("is_current", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("size_bytes", sa.Integer, nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("saved_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("saved_by", sa.String(128), nullable=False),
        sa.Column("source", sa.String(32), nullable=False, server_default="editor"),
    )
    op.create_index(
        "ix_dv_scope",
        "document_version",
        ["customer_id", "device_id", "milestone_id", "tg_name"],
    )
    op.create_index("ix_dv_path", "document_version", ["view_relative_path"])
    op.create_index("ix_dv_saved_at", "document_version", ["saved_at"])


def downgrade() -> None:
    op.drop_index("ix_dv_saved_at", table_name="document_version")
    op.drop_index("ix_dv_path", table_name="document_version")
    op.drop_index("ix_dv_scope", table_name="document_version")
    op.drop_table("document_version")
