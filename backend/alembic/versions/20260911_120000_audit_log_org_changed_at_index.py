"""Composite index on audit_log (organization_id, changed_at) for the change cursor.

The per-org change cursor (``GET /api/organizations/{id}/changes/cursor``) asks
one question every 20 seconds per open browser tab: "what is the newest
``changed_at`` for this organisation, and how many rows are there?". The two
existing single-column indexes (``idx_audit_log_org``, ``idx_audit_log_changed_at``)
force Postgres to pick one and filter or sort the rest; a composite index with
``changed_at DESC`` answers both with an index-only scan.

Revision ID: auditorgts1
Revises: cdmdrop001
Create Date: 2026-09-11 12:00:00
"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'auditorgts1'
down_revision: Union[str, None] = 'cdmdrop001'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

INDEX_NAME = "idx_audit_log_org_changed_at"


def upgrade() -> None:
    op.create_index(
        INDEX_NAME,
        "audit_log",
        ["organization_id", "changed_at"],
        unique=False,
        postgresql_ops={"changed_at": "DESC"},
        if_not_exists=True,
    )


def downgrade() -> None:
    op.drop_index(INDEX_NAME, table_name="audit_log", if_exists=True)
