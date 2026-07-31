"""Make audit_log.changed_by_user_id nullable to match its ON DELETE SET NULL FK

The foreign key ``audit_log.changed_by_user_id -> users.id`` is declared
``ON DELETE SET NULL`` while the column itself is ``NOT NULL``. Those two
statements cannot both be satisfied: the moment Postgres tries to honour the
SET NULL it violates the not-null constraint, so *any* ``DELETE FROM users``
fails with::

    NotNullViolationError: null value in column "changed_by_user_id"
    of relation "audit_log" violates not-null constraint

Two live callers are blocked by this today:

* ``backend/api/database_stats.py`` — the database restore endpoint, which
  could never complete a restore (reproduced against the dev stack: HTTP 500).
* ``backend/cli/admin.py`` — ``await db.delete(user)`` in the admin CLI.

Nullable is the correct side to relax rather than changing the FK to CASCADE:
audit rows are the tamper record of who changed what, and they must outlive the
account that made the change. ``action_source`` (``models.py:1864`` — ui, api_key,
mcp, system) already records *how* the change was made independently of *who*
made it, so a null ``changed_by_user_id`` reads unambiguously as "the acting
account has since been deleted" — which is exactly what SET NULL means.

Downgrade is intentionally lossy-safe: rows that have accumulated a NULL
cannot be reconstructed, so the downgrade backfills nothing and will fail if
any NULL exists. That is the honest behaviour — silently deleting audit rows
to restore a constraint would destroy the record this table exists to keep.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'auditlognull01'
down_revision = 'cdm4consol001'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        'audit_log',
        'changed_by_user_id',
        existing_type=sa.dialects.postgresql.UUID(as_uuid=True),
        nullable=True,
    )


def downgrade() -> None:
    # Fails loudly if any NULL is present rather than deleting audit history.
    op.alter_column(
        'audit_log',
        'changed_by_user_id',
        existing_type=sa.dialects.postgresql.UUID(as_uuid=True),
        nullable=False,
    )
