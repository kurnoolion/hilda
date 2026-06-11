"""Baseline schema — all storage tables from Base.metadata.

Revision ID: 0001_baseline
Revises: None

Baseline uses metadata-driven create/drop (idempotent, reversible per the
MODULE.md Alembic invariants); subsequent revisions use explicit op.* DDL.
"""
from alembic import op

from core.src.storage.db import Base

revision = "0001_baseline"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    Base.metadata.create_all(op.get_bind())


def downgrade() -> None:
    Base.metadata.drop_all(op.get_bind())
