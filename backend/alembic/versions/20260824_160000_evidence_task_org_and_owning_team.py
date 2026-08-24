"""Evidence collection tasks gain an organisation and an optional owning team.

Additive, #822 phase 4, section 6. Nothing existing is altered or removed:
``assigned_user_id`` keeps its column, its foreign key and its behaviour.

**A task inherits, it does not own.** An ``EvidenceCollectionTask`` is an
``ON DELETE CASCADE`` child of an evidence item that already carries team
ownership, and it is atomic by construction — one title, one due date, one
status, one doer. A join table would model a cardinality that does not exist
and would create a second source of truth that can drift from its parent. So
one nullable override column instead:

* ``owning_team_id IS NULL`` — inherit from the parent evidence item. The
  common case, zero user effort, no drift possible.
* ``owning_team_id`` set — an override, for the case that motivates the column
  at all: ``task_type`` already enumerates ``setup``, ``collection`` and
  ``review``, and on one evidence item those are routinely different functions.
  Engineering wires up the log export, the platform collects it, GRC signs it
  off.

``organization_id`` is not bookkeeping
--------------------------------------

``evidence_collection_tasks`` has, until this migration, **no organisation
column at all** — its tenancy is only transitive, through
``evidence_tracking``. That is why it needs one: a composite foreign key needs
a local ``organization_id`` to join through, and without the composite foreign
key nothing at the database level stops a task pointing at another tenant's
team. This is the cross-tenant isolation control for this table, and a
service-layer check is not a substitute for it — a service-layer check is one
forgotten call site away from being no check at all.

Two composite foreign keys, not one, for the reason phase 1 documented on
``team_members`` and phase 3 repeated on the assignment tables: one alone is a
half-open door. With only the team-side check, a row could name a *victim*
organisation's ``evidence_tracking_id`` while setting ``organization_id`` to
the attacker's own org; the team check would then verify the attacker's team
against the attacker's own org and let the row through, hanging one tenant's
task off another tenant's evidence item.

* ``fk_evidence_collection_tasks_evidence_org`` — the parent evidence item
  belongs to this organisation. Targets ``uq_evidence_tracking_org_id``, added
  in phase 3.
* ``fk_evidence_collection_tasks_team_org`` — and so does the team. Targets
  ``uq_teams_org_id``, added in phase 1.

Backfill, then NOT NULL
-----------------------

``organization_id`` is ``NOT NULL`` and the table has rows, so it cannot be
added in one step without inventing a default. Three steps instead: add it
nullable, backfill it from the parent evidence item, then constrain it. The
backfill is a mechanical join along an existing ``NOT NULL`` foreign key —
every task has exactly one parent and that parent has exactly one
organisation, so the result is derived, not guessed. ``SET NOT NULL`` is its
own verification: if any row failed to resolve, the ALTER fails and the whole
migration rolls back rather than half-applying.

Nothing here reads ``evidence_tracking.owner``. That column holds dirty
free-text labels — team names alongside person names, ``"TBD"``, empty
strings, and per-org spelling variants — and inferring team ownership from it
would write junk into every tenant at once with no way back. That
reconciliation is an operator-run CLI in phase 7, run per tenant, dry-run
first, with a human looking at the proposal.

``ON DELETE SET NULL``, on one column only
------------------------------------------

Deleting a team must orphan the task back to inheriting from its parent, never
delete the task — so the team-side constraint is ``SET NULL``, not
``CASCADE``. A plain ``ON DELETE SET NULL`` on a composite foreign key nulls
*every* referencing column, which would try to null ``organization_id`` too
and fail against its ``NOT NULL``, making the team undeletable. PostgreSQL 15
added the column list that says which one to null; ``organization_id`` is left
alone and the row falls back to inheritance, which is exactly the intent.

> This is the repository's first use of ``ON DELETE SET NULL (column)``, which
> requires **PostgreSQL 15 or newer**. That is the version this platform
> already ships, pins and documents everywhere (``docker-compose.yml``,
> ``README.md``, the AWS RDS instances), so it moves no floor — but it does
> make the floor load-bearing, where previously it was only conventional.

Under the default ``MATCH SIMPLE``, a composite foreign key with any NULL
column is not enforced at all. That is the behaviour the inherit case wants:
``owning_team_id IS NULL`` means "no team named here", and the constraint
stands aside rather than demanding a match.

Revision ID: evtaskteam1
Revises: invitemembertype1
Create Date: 2026-08-24 16:00:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID


revision: str = 'evtaskteam1'
down_revision: Union[str, None] = 'invitemembertype1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Step 1 of 3: nullable, because the table has rows and there is no honest
    # default for "which tenant does this belong to".
    op.add_column(
        'evidence_collection_tasks',
        sa.Column('organization_id', UUID(as_uuid=True), nullable=True),
    )

    # Step 2 of 3: derive it. evidence_tracking_id is NOT NULL with a real
    # foreign key behind it, so this join resolves for every existing row.
    op.execute(
        """
        UPDATE evidence_collection_tasks AS t
           SET organization_id = e.organization_id
          FROM evidence_tracking AS e
         WHERE e.id = t.evidence_tracking_id
        """
    )

    # Step 3 of 3: and now constrain it. This doubles as the check on step 2 —
    # a row the backfill missed aborts the migration here, whole.
    op.alter_column(
        'evidence_collection_tasks', 'organization_id', nullable=False,
    )

    # NULL is the default and the common case: inherit from the evidence item.
    op.add_column(
        'evidence_collection_tasks',
        sa.Column('owning_team_id', UUID(as_uuid=True), nullable=True),
    )

    # Cross-tenant isolation, half one: the parent evidence item belongs to
    # this organisation. CASCADE matches the existing single-column foreign key
    # to evidence_tracking — deleting an evidence item already takes its tasks
    # with it, and this constraint must not contradict that.
    op.create_foreign_key(
        'fk_evidence_collection_tasks_evidence_org',
        'evidence_collection_tasks', 'evidence_tracking',
        ['organization_id', 'evidence_tracking_id'], ['organization_id', 'id'],
        ondelete='CASCADE',
    )

    # Cross-tenant isolation, half two: the team belongs to that same
    # organisation. SET NULL on owning_team_id alone (PostgreSQL 15+): deleting
    # a team returns its tasks to inheriting from their evidence item and
    # leaves organization_id, which is NOT NULL, untouched.
    #
    # Raw SQL rather than op.create_foreign_key, because SQLAlchemy's DDL
    # compiler validates the referential action against a fixed set —
    # RESTRICT|CASCADE|SET NULL|NO ACTION|SET DEFAULT — and rejects the column
    # list outright ("Unexpected SQL phrase"). The phrase is valid PostgreSQL
    # 15; it is SQLAlchemy that has not caught up, so the DDL is emitted
    # directly. op.drop_constraint in downgrade() is unaffected.
    op.execute(
        """
        ALTER TABLE evidence_collection_tasks
          ADD CONSTRAINT fk_evidence_collection_tasks_team_org
          FOREIGN KEY (organization_id, owning_team_id)
          REFERENCES teams (organization_id, id)
          ON DELETE SET NULL (owning_team_id)
        """
    )

    # "Which tasks does this team own" is a phase 4 read, and the composite
    # foreign key above is organization_id-leftmost, so it does not serve it.
    # It also gives the team-delete referential action an index instead of a
    # sequential scan over every task in the deployment.
    op.create_index(
        'ix_evidence_collection_tasks_owning_team_id',
        'evidence_collection_tasks', ['owning_team_id'],
    )
    # Deliberately no index on organization_id alone. Nothing reads this table
    # by tenant today without also naming an evidence item, and an index
    # nobody reads is a write cost with no return. When the phase 4 work queue
    # starts filtering tasks by organisation and due date directly, that query
    # earns its own index in the migration that introduces it.

    # Notification dedup access path. #822 moves the duplicate check from
    # (user_id, type, reference_id, created_at) to a notification key of
    # type + reference_id + date, because with a recipient set rather than a
    # single recipient the user_id-leading check re-notifies whoever already
    # got the message after a partially-failed run. Dropping user_id from the
    # front of that predicate also drops it off ix_notifications_user, which
    # is the only index that served it — leaving the daily scheduler doing one
    # sequential scan of a table that is never pruned, per item, per run.
    #
    # This is an index, not state: the escalation-threshold question ("has the
    # 7-day threshold already fired for this item?") is answerable from the
    # rows already in this table, and no column is added for it.
    op.create_index(
        'ix_notifications_type_reference_created',
        'notifications', ['type', 'reference_id', 'created_at'],
    )


def downgrade() -> None:
    op.drop_index('ix_notifications_type_reference_created',
                  table_name='notifications')
    op.drop_index('ix_evidence_collection_tasks_owning_team_id',
                  table_name='evidence_collection_tasks')
    op.drop_constraint('fk_evidence_collection_tasks_team_org',
                       'evidence_collection_tasks', type_='foreignkey')
    op.drop_constraint('fk_evidence_collection_tasks_evidence_org',
                       'evidence_collection_tasks', type_='foreignkey')
    op.drop_column('evidence_collection_tasks', 'owning_team_id')
    op.drop_column('evidence_collection_tasks', 'organization_id')
