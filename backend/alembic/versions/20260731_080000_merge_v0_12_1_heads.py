"""Merge the two v0.12.1 heads into a single head.

v0.12.1 shipped two migrations that both revise cdm4consol001 —
cdm5ingstart01 (20260730_140000_cdm_ingest_started_at) and
auditlognull01 (20260730_223000_audit_log_changed_by_user_id_nullable) —
leaving the migration graph with two heads and no merge point.
scripts/upgrade.sh runs `alembic upgrade head` (singular), which
fails closed on multiple heads, so every self-hosted upgrade to
v0.12.1 aborts and rolls back. This no-op merge revision joins the
two branches back into a single head.

Revision ID: merge0121heads
Revises: cdm5ingstart01, auditlognull01
Create Date: 2026-07-31 08:00:00
"""

# revision identifiers, used by Alembic.
revision = 'merge0121heads'
down_revision = ('cdm5ingstart01', 'auditlognull01')
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
