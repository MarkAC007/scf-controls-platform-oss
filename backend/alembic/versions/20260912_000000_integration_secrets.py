"""Integration secrets, platform audit log, and encrypted credential columns

Issue #947 (zero-touch credential provisioning), contract §3b.

This migration deliberately needs NO encryption key. It only widens columns and
adds a deterministic lookup hash, so it runs to completion on a database full of
legacy plaintext rows, on an installation that has never had SCF_SECRET_KEY set.
Encrypting the existing rows is a separate, explicitly-invoked step
(`python -m cli.admin backfill-encrypt`).

Widening `invite_token` and `webhook_endpoints.secret` to Text is required
because Fernet ciphertext is far longer than the plaintext it replaces.
Dropping the uniqueness on `invite_token` is required because ciphertext is
non-deterministic: the same token encrypts to a different value every time, so a
unique constraint on it would be meaningless and an equality lookup impossible.
`invite_token_hash` restores both properties.

Revision ID: intsec947a1
Revises: auditorgts1
Create Date: 2026-09-12 00:00:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "intsec947a1"
down_revision: Union[str, None] = "auditorgts1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# (table, old unique constraint name, old unique index name, new unique index name)
_INVITE_TABLES = (
    (
        "organization_invites",
        "uq_organization_invites_token",
        "idx_org_invites_token",
        "uq_organization_invites_invite_token_hash",
    ),
    (
        "consultant_invites",
        "uq_consultant_invites_token",
        "ix_consultant_invites_token",
        "uq_consultant_invites_invite_token_hash",
    ),
)


def upgrade() -> None:
    # ------------------------------------------------------------------
    # 1. integration_secrets — encrypted tier-3 credential store
    # ------------------------------------------------------------------
    op.create_table(
        "integration_secrets",
        sa.Column("name", sa.String(64), primary_key=True, nullable=False),
        sa.Column("value_ciphertext", sa.Text(), nullable=False),
        sa.Column("key_version", sa.SmallInteger(), nullable=False, server_default="1"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=True,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=True,
            server_default=sa.text("now()"),
        ),
        sa.Column("updated_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("updated_by_label", sa.String(200), nullable=True),
        sa.ForeignKeyConstraint(
            ["updated_by_user_id"], ["users.id"], ondelete="SET NULL"
        ),
    )

    # ------------------------------------------------------------------
    # 2. platform_audit_log — no organisation, and no value columns at all
    # ------------------------------------------------------------------
    op.create_table(
        "platform_audit_log",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("entity_type", sa.String(50), nullable=False),
        sa.Column("entity_id", sa.String(200), nullable=False),
        sa.Column("action", sa.String(40), nullable=False),
        sa.Column("actor", sa.String(200), nullable=False),
        sa.Column("actor_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("ip_address", sa.String(45), nullable=True),
        sa.Column("user_agent", sa.Text(), nullable=True),
        sa.Column("action_source", sa.String(20), nullable=True),
        sa.Column("request_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["actor_user_id"], ["users.id"], ondelete="SET NULL"
        ),
    )
    op.execute(
        "CREATE INDEX ix_platform_audit_log_entity_created "
        "ON platform_audit_log (entity_type, created_at DESC)"
    )

    # ------------------------------------------------------------------
    # 3. webhook_endpoints.secret — widen for ciphertext
    # ------------------------------------------------------------------
    op.alter_column(
        "webhook_endpoints",
        "secret",
        existing_type=sa.String(70),
        type_=sa.Text(),
        existing_nullable=False,
    )

    # ------------------------------------------------------------------
    # 4. invite tokens — widen, add the lookup hash, move uniqueness onto it
    # ------------------------------------------------------------------
    for table, uq_name, ix_name, new_ix in _INVITE_TABLES:
        op.alter_column(
            table,
            "invite_token",
            existing_type=sa.String(64),
            type_=sa.Text(),
            existing_nullable=False,
        )
        op.add_column(table, sa.Column("invite_token_hash", sa.String(64), nullable=True))
        # sha256() is a core Postgres function from 11 onwards — no pgcrypto needed.
        op.execute(
            f"UPDATE {table} "
            f"SET invite_token_hash = encode(sha256(convert_to(invite_token, 'UTF8')), 'hex') "
            f"WHERE invite_token_hash IS NULL"
        )
        op.alter_column(table, "invite_token_hash", existing_type=sa.String(64), nullable=False)
        op.create_index(new_ix, table, ["invite_token_hash"], unique=True)
        # Old uniqueness on the token itself: meaningless once the column holds
        # non-deterministic ciphertext. Dropped by name, IF EXISTS because an
        # install may carry one, the other, or both.
        # Identifiers come from the _INVITE_TABLES constant above, never from
        # input. Raw DDL only because alembic 1.13 has no if_exists on
        # drop_constraint; drop_index has it, so that one is a real op.
        # nosemgrep: python.lang.security.audit.formatted-sql-query.formatted-sql-query, python.sqlalchemy.security.sqlalchemy-execute-raw-query.sqlalchemy-execute-raw-query
        op.execute(f"ALTER TABLE {table} DROP CONSTRAINT IF EXISTS {uq_name}")
        op.drop_index(ix_name, table_name=table, if_exists=True)


def downgrade() -> None:
    # NOTE: this is lossy if `backfill-encrypt` or any write has encrypted the
    # token columns in the meantime — the restored unique constraint applies to
    # ciphertext, and the values will not fit back into String(64). Decrypt
    # first (there is no automated path; restore from backup instead).
    for table, uq_name, ix_name, new_ix in _INVITE_TABLES:
        op.drop_index(new_ix, table_name=table, if_exists=True)
        op.drop_column(table, "invite_token_hash")
        op.alter_column(
            table,
            "invite_token",
            existing_type=sa.Text(),
            type_=sa.String(64),
            existing_nullable=False,
        )
        op.create_unique_constraint(uq_name, table, ["invite_token"])
        op.create_index(ix_name, table, ["invite_token"], unique=True)

    op.alter_column(
        "webhook_endpoints",
        "secret",
        existing_type=sa.Text(),
        type_=sa.String(70),
        existing_nullable=False,
    )

    op.execute("DROP INDEX IF EXISTS ix_platform_audit_log_entity_created")
    op.drop_table("platform_audit_log")
    op.drop_table("integration_secrets")
