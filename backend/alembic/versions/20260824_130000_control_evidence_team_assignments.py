"""Team assignment of controls and evidence, with tenant isolation in the database.

Two additive tables (#822 phase 3). Nothing existing is altered.

``control_team_assignments`` and ``evidence_team_assignments`` attach a team —
the organisation-scoped teams created in phase 1 — to a scoped control or an
evidence tracking record. Both carry an ``is_accountable`` flag, and a partial
unique index allows **at most** one accountable team per control and per
evidence item. At most, never exactly: a control that nobody has assigned yet
has no accountable team, which is the state every control is in until somebody
picks one, and nothing here may require a row to exist.

These are deliberately *not* polymorphic. The existing ``assignments`` table
resolves its target through ``assignable_type`` plus a bare
``assignable_id`` with no foreign key behind it, which means the database
cannot tell a live control from a deleted one, or from a typo. These two
tables name their target directly and let Postgres enforce it.

Both tables carry a denormalised ``organization_id`` next to the target id and
the team id, and two composite foreign keys that force all three to agree:

* ``(organization_id, <target>_id) -> scoped_controls`` / ``evidence_tracking``
  — the control or evidence record belongs to this organisation.
* ``(organization_id, team_id) -> teams`` — and so does the team.

Both halves are needed, for the reason phase 1 documented on ``team_members``:
one constraint alone is a half-open door. With only the team-side check, a row
could name a *victim* organisation's ``scoped_control_id`` while setting
``organization_id`` to the attacker's own org; the team check would then
cheerfully verify the attacker's team against the attacker's own org and let
the row through, publishing one tenant's team onto another tenant's control.

The composite foreign keys need a composite target, and neither
``scoped_controls`` nor ``evidence_tracking`` had one — their only uniques were
the primary key and the ``(organization_id, <business key>)`` pair. This
migration adds ``uq_scoped_controls_org_id`` and
``uq_evidence_tracking_org_id`` purely to be foreign-key targets, exactly as
phase 1 added ``uq_teams_org_id``. Adding a constraint drops and alters
nothing, but it does build a unique index over an existing table and therefore
takes a brief ``ACCESS EXCLUSIVE`` lock on deploy — on tenants with large
control sets that is a short write stall, not a no-op.

All of this lives in the database on purpose. A service-layer check is one
forgotten call site away from being no check at all, and this is the control
that keeps one tenant's teams off another tenant's compliance records.

Deliberately absent: any assignment data. This migration creates no rows and
reads no free-text owner column. ``scoped_controls.owner``,
``scoped_controls.assigned_to`` and ``evidence_tracking.owner`` hold team
labels that were never validated against anything, and deriving assignments
from them would write junk into every tenant at once with no way back. That
reconciliation is an operator-run CLI in a later phase, run per tenant, with a
human looking at the result.

Also deliberately absent: any change to what already works.
``scoped_controls.assigned_user_id``, ``scoped_controls.owner_user_id`` and
the polymorphic ``assignments`` table are untouched and keep functioning.

Revision ID: ctrlteamassign1
Revises: teamsfunctions1
Create Date: 2026-08-24 13:00:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID


revision: str = 'ctrlteamassign1'
down_revision: Union[str, None] = 'teamsfunctions1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Composite foreign-key targets. Redundant against each table's primary key
    # as a uniqueness statement, and not redundant at all as a foreign-key
    # target: they are what lets the assignment tables below prove their
    # denormalised organization_id matches the record they point at. Each
    # builds a unique index over an existing table, so each takes a brief
    # ACCESS EXCLUSIVE lock on deploy.
    op.create_unique_constraint(
        'uq_scoped_controls_org_id', 'scoped_controls', ['organization_id', 'id'],
    )
    op.create_unique_constraint(
        'uq_evidence_tracking_org_id', 'evidence_tracking', ['organization_id', 'id'],
    )

    op.create_table(
        'control_team_assignments',
        sa.Column('id', UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text('gen_random_uuid()')),
        sa.Column('scoped_control_id', UUID(as_uuid=True), nullable=False),
        sa.Column('team_id', UUID(as_uuid=True), nullable=False),
        # Denormalised on purpose: it is the column the composite foreign keys
        # below join through. Its correctness is enforced, not assumed.
        sa.Column('organization_id', UUID(as_uuid=True), nullable=False),
        sa.Column('is_accountable', sa.Boolean(), nullable=False,
                  server_default=sa.text('false')),
        sa.Column('assigned_at', sa.DateTime(timezone=False), nullable=False,
                  server_default=sa.text('now()')),
        sa.Column('assigned_by_user_id', UUID(as_uuid=True),
                  sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
        # The directly readable "this belongs to a control" edge, and the one
        # the ORM and schema tooling follow. The tenant check is the composite
        # below; this one says what the row is about.
        sa.ForeignKeyConstraint(
            ['scoped_control_id'], ['scoped_controls.id'],
            name='fk_control_team_assignments_control', ondelete='CASCADE',
        ),
        # Cross-tenant isolation, half one: the control belongs to this
        # organisation. Targets uq_scoped_controls_org_id.
        sa.ForeignKeyConstraint(
            ['organization_id', 'scoped_control_id'],
            ['scoped_controls.organization_id', 'scoped_controls.id'],
            name='fk_control_team_assignments_control_org', ondelete='CASCADE',
        ),
        # Cross-tenant isolation, half two: the team belongs to that same
        # organisation. Without this, organization_id could name the caller's
        # own org while scoped_control_id named somebody else's control, and
        # half one would validate it. Targets uq_teams_org_id.
        sa.ForeignKeyConstraint(
            ['organization_id', 'team_id'],
            ['teams.organization_id', 'teams.id'],
            name='fk_control_team_assignments_team_org', ondelete='CASCADE',
        ),
        sa.UniqueConstraint('scoped_control_id', 'team_id',
                            name='uq_control_team_assignments_control_team'),
    )
    # At most one accountable team per control. Partial, so a control nobody has
    # assigned yet — every control, until somebody picks an owner — is legal.
    op.create_index(
        'uq_control_accountable_team', 'control_team_assignments',
        ['scoped_control_id'], unique=True,
        postgresql_where=sa.text('is_accountable'),
    )
    # "Which controls does this team own" is the hot read, and team_id has no
    # index of its own — uq_control_team_assignments_control_team is
    # control-leftmost. This also gives the cascade from teams something to use
    # instead of a sequential scan; team_id is a UUID, so filtering the
    # organization_id half afterwards costs nothing.
    op.create_index('ix_control_team_assignments_team_id',
                    'control_team_assignments', ['team_id'])
    # Deliberately no index on scoped_control_id alone: the unique constraint is
    # control-leftmost and already serves both those lookups and the cascade
    # from scoped_controls. Deliberately no index on organization_id alone:
    # nothing reads this table by tenant without also naming a control or a
    # team, and an index nobody reads is a write cost with no return.

    op.create_table(
        'evidence_team_assignments',
        sa.Column('id', UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text('gen_random_uuid()')),
        sa.Column('evidence_tracking_id', UUID(as_uuid=True), nullable=False),
        sa.Column('team_id', UUID(as_uuid=True), nullable=False),
        # Denormalised for the same reason, enforced by the same pair of
        # composite foreign keys.
        sa.Column('organization_id', UUID(as_uuid=True), nullable=False),
        sa.Column('is_accountable', sa.Boolean(), nullable=False,
                  server_default=sa.text('false')),
        sa.Column('assigned_at', sa.DateTime(timezone=False), nullable=False,
                  server_default=sa.text('now()')),
        sa.Column('assigned_by_user_id', UUID(as_uuid=True),
                  sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
        sa.ForeignKeyConstraint(
            ['evidence_tracking_id'], ['evidence_tracking.id'],
            name='fk_evidence_team_assignments_evidence', ondelete='CASCADE',
        ),
        # Cross-tenant isolation, half one. Targets uq_evidence_tracking_org_id.
        sa.ForeignKeyConstraint(
            ['organization_id', 'evidence_tracking_id'],
            ['evidence_tracking.organization_id', 'evidence_tracking.id'],
            name='fk_evidence_team_assignments_evidence_org', ondelete='CASCADE',
        ),
        # Cross-tenant isolation, half two. Targets uq_teams_org_id.
        sa.ForeignKeyConstraint(
            ['organization_id', 'team_id'],
            ['teams.organization_id', 'teams.id'],
            name='fk_evidence_team_assignments_team_org', ondelete='CASCADE',
        ),
        sa.UniqueConstraint('evidence_tracking_id', 'team_id',
                            name='uq_evidence_team_assignments_evidence_team'),
    )
    # At most one accountable team per evidence item, on the same terms.
    op.create_index(
        'uq_evidence_accountable_team', 'evidence_team_assignments',
        ['evidence_tracking_id'], unique=True,
        postgresql_where=sa.text('is_accountable'),
    )
    op.create_index('ix_evidence_team_assignments_team_id',
                    'evidence_team_assignments', ['team_id'])


def downgrade() -> None:
    op.drop_index('ix_evidence_team_assignments_team_id',
                  table_name='evidence_team_assignments')
    op.drop_index('uq_evidence_accountable_team',
                  table_name='evidence_team_assignments')
    op.drop_table('evidence_team_assignments')
    op.drop_index('ix_control_team_assignments_team_id',
                  table_name='control_team_assignments')
    op.drop_index('uq_control_accountable_team',
                  table_name='control_team_assignments')
    op.drop_table('control_team_assignments')
    # Last, and in this order: the tables above pointed at these two, so they
    # cannot be dropped until nothing references them.
    op.drop_constraint('uq_evidence_tracking_org_id', 'evidence_tracking',
                       type_='unique')
    op.drop_constraint('uq_scoped_controls_org_id', 'scoped_controls',
                       type_='unique')
