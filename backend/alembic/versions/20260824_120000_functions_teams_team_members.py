"""Functions, teams and team members: accountability with tenant isolation in the database.

Three additive tables (#822 phase 1). Nothing existing is altered.

``functions`` is platform-static: fourteen seeded rows naming the business
functions a control can be owned by. It is seeded here, with *fixed* ids
rather than ``gen_random_uuid()``, because every tenant references the same
fourteen rows — a function that has a different id in staging than in
production is a foot-gun for every later phase that wants to ship a mapping.

``teams`` is organisation-scoped and free-form: a tenant names its own teams
and points each at one function.

``team_members`` is the reason this migration is careful. It carries a
denormalised ``organization_id`` next to ``team_id`` and ``user_id``, and two
composite foreign keys that force all three to agree:

* ``(organization_id, user_id) -> organization_members`` — you cannot be on a
  team of an organisation you are not a member of, and losing your membership
  removes you from its teams in the same statement.
* ``(organization_id, team_id) -> teams`` — the other half of the same rule.
  Without it, a row could name a *victim* organisation's ``team_id`` while
  setting ``organization_id`` to the attacker's own org; the first constraint
  would then cheerfully verify the attacker's membership of their own org and
  let the row through. One constraint is a half-open door.

Both live in the database on purpose. A service-layer check is one forgotten
call site away from being no check at all, and this is the control that keeps
one tenant's people out of another tenant's accountability records.

Two partial unique indexes enforce **at most** one ``primary`` and **at most**
one ``delegate`` per team. At most, never exactly: a team that has just been
created has no members yet, and nothing here may require a row to exist.

Deliberately absent: any team data. This migration reads no free-text owner
column and invents no teams. Deriving teams from ``evidence_tracking.owner``
or ``vendor_action_items.owner_name`` would write junk into every tenant with
no way back; that reconciliation is an operator-run CLI in a later phase.

Revision ID: teamsfunctions1
Revises: auditappendonly1
Create Date: 2026-08-24 12:00:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID


revision: str = 'teamsfunctions1'
down_revision: Union[str, None] = 'auditappendonly1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


MEMBERSHIP_ROLES = ('primary', 'delegate', 'member')

# Fixed ids, not generated. Reproduce with:
#   NS = uuid5(NAMESPACE_DNS, 'functions.scf.compliancegenie.io')
#      -> 95b9c466-f07c-52d9-a134-0bb60bbe3797
#   id = uuid5(NS, key)
FUNCTION_SEED = (
    (
        '024f3da6-eb1d-5656-a81d-94a24b39abcf',
        'governance_risk_compliance',
        'Governance, Risk & Compliance',
        'Owns the control framework itself — policy, risk treatment, audit '
        'readiness and the evidence that the programme is working.',
    ),
    (
        '853c3e67-a49d-55d6-a3ff-643c90d082fc',
        'security_operations',
        'Security Operations',
        'Runs detection, monitoring, triage and incident response day to day.',
    ),
    (
        '2b6c36ea-bee1-52cf-94d4-1aa6e7d9fe4e',
        'security_engineering',
        'Security Engineering',
        'Builds and maintains the security tooling and hardened platform '
        'controls that other functions rely on.',
    ),
    (
        '413e0998-1e8a-5777-af51-57d1a6f5c206',
        'it_operations',
        'IT Operations',
        'Runs the corporate estate — endpoints, networks, infrastructure, '
        'patching and backup.',
    ),
    (
        'b0dc9181-ea09-5dde-b7c8-1fe43c33faee',
        'software_engineering',
        'Software Engineering / DevSecOps',
        'Owns secure development, the build and release pipeline, and the '
        'controls embedded in them.',
    ),
    (
        '934c2b55-6091-5c10-8e16-bb201d19cd99',
        'identity_access_management',
        'Identity & Access Management',
        'Owns joiner/mover/leaver, authentication, privileged access and '
        'periodic access review.',
    ),
    (
        '4acb02f2-abb8-553b-8029-e0e68aeb23c7',
        'data_privacy',
        'Data Protection & Privacy',
        'Owns personal data handling, lawful basis, data subject rights and '
        'privacy impact assessment.',
    ),
    (
        '749d4f70-f65c-592f-8c98-22699d182edb',
        'human_resources',
        'Human Resources',
        'Owns screening, onboarding and offboarding, disciplinary process and '
        'security awareness training.',
    ),
    (
        '8a0879c2-1db0-5758-a403-25e2d360114b',
        'legal',
        'Legal',
        'Owns contractual and regulatory obligations, and the legal review '
        'behind commitments the organisation makes.',
    ),
    (
        'aabb87c1-5a2d-590e-a36e-80ab70f5977a',
        'finance',
        'Finance',
        'Owns financial controls, segregation of duties over spend, and the '
        'budget that funds remediation.',
    ),
    (
        '3bcfc8a4-6540-5f64-a36a-1c53950f4d72',
        'procurement_vendor_management',
        'Procurement & Vendor Management',
        'Owns supplier due diligence, contractual security terms and ongoing '
        'third-party risk review.',
    ),
    (
        '326f3f30-4244-550d-b130-5ff3bcfa91ce',
        'facilities_physical_security',
        'Facilities & Physical Security',
        'Owns physical access, environmental controls and the security of '
        'premises holding equipment or records.',
    ),
    (
        '586f6d91-8711-5b46-a9cb-80122959588e',
        'business_continuity',
        'Business Continuity & Resilience',
        'Owns continuity planning, disaster recovery and the exercises that '
        'prove either one works.',
    ),
    (
        '0b8a4fef-4ba4-51bf-a609-89a413976f69',
        'executive_leadership',
        'Executive Leadership',
        'Holds accountability for the programme — risk acceptance, resourcing '
        'and management review.',
    ),
)


def upgrade() -> None:
    op.create_table(
        'functions',
        sa.Column('id', UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text('gen_random_uuid()')),
        sa.Column('key', sa.String(50), nullable=False),
        sa.Column('name', sa.String(100), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('display_order', sa.Integer(), nullable=True),
        sa.Column('is_active', sa.Boolean(), nullable=False,
                  server_default=sa.text('true')),
        sa.UniqueConstraint('key', name='uq_functions_key'),
    )

    functions_table = sa.table(
        'functions',
        sa.column('id', UUID(as_uuid=True)),
        sa.column('key', sa.String),
        sa.column('name', sa.String),
        sa.column('description', sa.Text),
        sa.column('display_order', sa.Integer),
        sa.column('is_active', sa.Boolean),
    )
    op.bulk_insert(
        functions_table,
        [
            {
                'id': function_id,
                'key': key,
                'name': name,
                'description': description,
                'display_order': display_order,
                'is_active': True,
            }
            for display_order, (function_id, key, name, description)
            in enumerate(FUNCTION_SEED, start=1)
        ],
    )

    op.create_table(
        'teams',
        sa.Column('id', UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text('gen_random_uuid()')),
        sa.Column('organization_id', UUID(as_uuid=True),
                  sa.ForeignKey('organizations.id', ondelete='CASCADE'),
                  nullable=False),
        # RESTRICT, not CASCADE: functions are platform-static. Removing one
        # out from under a tenant's teams would silently destroy their
        # accountability records, so the delete is refused instead.
        sa.Column('function_id', UUID(as_uuid=True),
                  sa.ForeignKey('functions.id', ondelete='RESTRICT'),
                  nullable=False),
        sa.Column('name', sa.String(255), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('is_active', sa.Boolean(), nullable=False,
                  server_default=sa.text('true')),
        sa.Column('created_at', sa.DateTime(timezone=False), nullable=False,
                  server_default=sa.text('now()')),
        sa.Column('updated_at', sa.DateTime(timezone=False), nullable=False,
                  server_default=sa.text('now()')),
        sa.Column('created_by_user_id', UUID(as_uuid=True),
                  sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
        sa.Column('updated_by_user_id', UUID(as_uuid=True),
                  sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
        sa.UniqueConstraint('organization_id', 'name', name='uq_teams_org_name'),
        # Redundant against the primary key as a uniqueness statement, and not
        # redundant at all as a foreign-key target: it is what lets
        # team_members prove its denormalised organization_id matches its
        # team's. Later phases need the same handle.
        sa.UniqueConstraint('organization_id', 'id', name='uq_teams_org_id'),
    )
    # Postgres does not index the referencing side of a foreign key, and the
    # RESTRICT check on functions scans this column.
    op.create_index('ix_teams_function_id', 'teams', ['function_id'])
    # Deliberately no index on organization_id alone: uq_teams_org_name is
    # organization-leftmost and already serves those lookups.

    op.create_table(
        'team_members',
        sa.Column('id', UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text('gen_random_uuid()')),
        sa.Column('team_id', UUID(as_uuid=True), nullable=False),
        # Denormalised on purpose: it is the column the composite foreign keys
        # below join through. Its correctness is enforced, not assumed.
        sa.Column('organization_id', UUID(as_uuid=True), nullable=False),
        # No direct foreign key to users.id — reaching users through
        # organization_members is what makes the membership check unavoidable.
        sa.Column('user_id', UUID(as_uuid=True), nullable=False),
        sa.Column('membership_role', sa.String(20), nullable=False),
        sa.Column('added_at', sa.DateTime(timezone=False), nullable=False,
                  server_default=sa.text('now()')),
        sa.Column('added_by_user_id', UUID(as_uuid=True),
                  sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
        sa.ForeignKeyConstraint(
            ['team_id'], ['teams.id'],
            name='fk_team_members_team', ondelete='CASCADE',
        ),
        # Cross-tenant isolation, half one: the user must be a member of this
        # organisation. Targets uq_organization_members_org_user.
        sa.ForeignKeyConstraint(
            ['organization_id', 'user_id'],
            ['organization_members.organization_id', 'organization_members.user_id'],
            name='fk_team_members_org_member', ondelete='CASCADE',
        ),
        # Cross-tenant isolation, half two: the team must belong to the same
        # organisation. Without this, organization_id could name the caller's
        # own org while team_id named somebody else's team, and half one would
        # validate it. Targets uq_teams_org_id.
        sa.ForeignKeyConstraint(
            ['organization_id', 'team_id'],
            ['teams.organization_id', 'teams.id'],
            name='fk_team_members_team_org', ondelete='CASCADE',
        ),
        sa.UniqueConstraint('team_id', 'user_id', name='uq_team_members_team_user'),
        sa.CheckConstraint(
            "membership_role IN ('primary', 'delegate', 'member')",
            name='ck_team_members_membership_role',
        ),
    )
    # At most one primary and at most one delegate per team. Partial, so a team
    # with no members at all — every team, the moment it is created — is legal.
    op.create_index(
        'uq_team_primary', 'team_members', ['team_id'], unique=True,
        postgresql_where=sa.text("membership_role = 'primary'"),
    )
    op.create_index(
        'uq_team_delegate', 'team_members', ['team_id'], unique=True,
        postgresql_where=sa.text("membership_role = 'delegate'"),
    )
    # "Which teams is this person on" is the hot read, and user_id has no
    # index of its own — the composite key is organization-leftmost.
    op.create_index('ix_team_members_user_id', 'team_members', ['user_id'])


def downgrade() -> None:
    op.drop_index('ix_team_members_user_id', table_name='team_members')
    op.drop_index('uq_team_delegate', table_name='team_members')
    op.drop_index('uq_team_primary', table_name='team_members')
    op.drop_table('team_members')
    op.drop_index('ix_teams_function_id', table_name='teams')
    op.drop_table('teams')
    op.drop_table('functions')
