"""Employment type on organisation membership, scoped to the membership.

One additive column (#822 phase 2). Nothing existing is altered.

``organization_members.member_type`` records whether a person is internal
staff or an external contractor. It sits on the *membership*, not on the
person, and that placement is the whole point of the column.

This platform is multi-tenant and runs a consultant portal, so one human being
routinely holds several memberships at once. The same consultant is permanent
staff at their own firm and an external contractor at each client they serve.
On ``users`` — a single global row per person — that is simply unrepresentable:
there is one field and several contradictory answers. Worse, whichever answer
won would be visible to every tenant that person belongs to, publishing one
organisation's employment relationship into another organisation's view of the
same user. On ``organization_members`` each tenant states its own relationship
and sees only its own.

The allowed values are enforced by a CHECK constraint rather than a Postgres
ENUM type. Adding a value to an enum is a type-level migration that has to be
coordinated across every environment; widening a CHECK is one
``ALTER TABLE ... DROP CONSTRAINT / ADD CONSTRAINT`` on a table whose rows
already satisfy it. Phase 2 ships two values and there is no reason to believe
that list is final. The rest of the codebase already settled this question the
same way — see ``ck_team_members_membership_role``.

``ix_organization_members_member_type`` is ``(organization_id, member_type)``
in that order, and not the reverse. Every read is already inside one tenant;
``member_type`` alone would be a two-value column across the whole table and
worth nothing as a leading key. Phase 2's reporting joins filter on exactly
this pair.

Deliberately absent: any backfill. ``upgrade()`` is DDL only — it does not
read a single row. Every existing membership becomes ``'internal'`` because
the server default says so, and by no other route. Guessing at contractor
status from email domains or free-text owner fields would be a silent,
irreversible, cross-tenant data write dressed up as a convenience; a wrong
guess is indistinguishable from a deliberate answer once it is in the column.
Organisations state their own contractors.

Also deliberately absent: any grant of authority. ``member_type`` is a label.
Authorisation stays entirely on ``organization_members.role``, which this
migration does not touch.

Revision ID: orgmembertype1
Revises: ctrlteamassign1
Create Date: 2026-08-24 14:00:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'orgmembertype1'
down_revision: Union[str, None] = 'ctrlteamassign1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # NOT NULL with a server default, so existing rows are filled by Postgres
    # in the same statement and no backfill pass exists to get wrong. Postgres
    # 11+ stores this default in the catalogue rather than rewriting the heap,
    # so the table is not copied.
    op.add_column(
        'organization_members',
        sa.Column('member_type', sa.String(30), nullable=False,
                  server_default='internal'),
    )
    # Named explicitly, like every other constraint here, so downgrade() can
    # name it back rather than guessing at a generated identifier.
    op.create_check_constraint(
        'ck_organization_members_member_type',
        'organization_members',
        "member_type IN ('internal', 'external_contractor')",
    )
    # Non-unique, and organisation-leftmost: a person may of course be an
    # external contractor at more than one organisation, and the existing
    # uq_organization_members_org_user already forbids the only duplicate that
    # matters. See the module docstring for why this column order.
    op.create_index(
        'ix_organization_members_member_type', 'organization_members',
        ['organization_id', 'member_type'],
    )


def downgrade() -> None:
    op.drop_index('ix_organization_members_member_type',
                  table_name='organization_members')
    op.drop_constraint('ck_organization_members_member_type',
                       'organization_members', type_='check')
    op.drop_column('organization_members', 'member_type')
