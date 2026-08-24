"""Employment type on the organisation invite, so the invite can carry it.

One additive column (#822 phase 2). Nothing existing is altered.

``organization_members.member_type`` (revision ``orgmembertype1``) gave a
membership an employment type. This gives the *invite* the same field, for the
reason the phase requires a ``member_type`` selector in ``InviteUserModal``:
a membership is created when an invite is accepted, so whatever the inviting
admin chooses has to survive the gap between sending and accepting. Without a
column to survive in, the selector would be a control that changes nothing —
the user picks "external contractor", the invite is accepted days later, and
the membership comes out internal. That silent-no-op control is the defect
#822 exists to fix, so reproducing it here would be a poor joke.

``organization_invites.role`` already makes this round trip; ``member_type``
simply travels beside it.

No index, unlike ``organization_members``. Invites are read by token on
acceptance and by organisation on the pending list, never filtered by
employment type; the table is small and short-lived besides. An index added
for symmetry with the members table would be write cost with no read to pay
for it.

The allowed values are a CHECK constraint rather than a Postgres ENUM, and the
values match ``ck_organization_members_member_type`` exactly — the invite is
the source of the membership's value, so a value legal here and illegal there
would be an invite that cannot be accepted. If that list ever widens, both
constraints have to widen together.

``OrganizationInvite`` had no ``__table_args__`` at all before this, so the
model side of the change adds the tuple. Its sibling ``ConsultantInvite``
shares only ``InviteMixin``, which carries behaviour (``is_expired``,
``is_pending``) and no table metadata, so nothing is inherited and nothing is
shadowed. ``consultant_invites`` is deliberately untouched: it is a different
flow with its own lifecycle — it creates an organisation rather than joining
one — and #822 phase 2 does not name it.

Deliberately absent: any backfill. ``upgrade()`` is DDL only. Pending invites
become ``'internal'`` because the server default says so, and by no other
route. An invite already in flight was composed by an admin who was never
shown the choice, so the only honest value is the default; guessing at it from
the invitee's email domain would put words in that admin's mouth.

Also deliberately absent: any grant of authority. ``member_type`` is a label.
Authorisation stays on ``role``, which this migration does not touch, along
with the token, the status machine, and expiry.

Revision ID: invitemembertype1
Revises: orgmembertype1
Create Date: 2026-08-24 15:00:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'invitemembertype1'
down_revision: Union[str, None] = 'orgmembertype1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # NOT NULL with a server default, so pending invites are filled by Postgres
    # in the same statement and no backfill pass exists to get wrong.
    op.add_column(
        'organization_invites',
        sa.Column('member_type', sa.String(30), nullable=False,
                  server_default='internal'),
    )
    # Same two values as ck_organization_members_member_type, and they must stay
    # the same two: this column's value is copied onto the membership when the
    # invite is accepted, so anything legal here and illegal there would be an
    # invite that can never be accepted.
    op.create_check_constraint(
        'ck_organization_invites_member_type',
        'organization_invites',
        "member_type IN ('internal', 'external_contractor')",
    )


def downgrade() -> None:
    op.drop_constraint('ck_organization_invites_member_type',
                       'organization_invites', type_='check')
    op.drop_column('organization_invites', 'member_type')
