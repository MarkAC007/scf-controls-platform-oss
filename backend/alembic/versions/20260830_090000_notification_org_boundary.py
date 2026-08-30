"""Add organization_id to notifications (#852).

Notifications previously had no org boundary: rows carried only user_id and
an untyped reference_id, so a user removed from an organization kept reading
that org's evidence keys through the bell indefinitely. This migration gives
every notification a tenant, following the evtaskteam1 three-step pattern
(add nullable -> backfill by join -> SET NOT NULL, where the constraint
doubles as the backfill's own verification).

Backfill derives the org from the referenced entity, per reference_type:

  control          -> scoped_controls.organization_id
  evidence         -> evidence_tracking.organization_id
  task             -> evidence_collection_tasks.organization_id
  team             -> teams.organization_id
  catalog          -> organization_reconciliation_runs.organization_id
  engagement_query -> engagement_queries -> audit_engagements.organization_id
  comment          -> comments.commentable_type/commentable_id, two hops via
                      the four commentable tables above

Rows whose reference target no longer exists cannot be attributed to any
tenant. They are also dead weight: their click-through navigation resolves
nothing. They are DELETED before SET NOT NULL rather than kept nullable —
a nullable tenant column would weaken the boundary for every future reader.

Unlike evidence_collection_tasks there is no per-org uniqueness target to
point a composite FK at (reference_id is untyped), so a plain FK to
organizations.id ON DELETE CASCADE is the ceiling here.

Revision ID: notiforg1
Revises: teamfunctions2
Create Date: 2026-08-30
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = 'notiforg1'
down_revision: Union[str, None] = 'teamfunctions2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Step 1: nullable — the table has rows and there is no honest default
    # for "which tenant does this belong to".
    op.add_column(
        'notifications',
        sa.Column('organization_id', UUID(as_uuid=True), nullable=True),
    )

    # Step 2: derive it from the referenced entity, one UPDATE per
    # reference_type so each join stays trivially reviewable.
    op.execute(
        """
        UPDATE notifications AS n
           SET organization_id = sc.organization_id
          FROM scoped_controls AS sc
         WHERE n.reference_type = 'control' AND sc.id = n.reference_id
        """
    )
    op.execute(
        """
        UPDATE notifications AS n
           SET organization_id = et.organization_id
          FROM evidence_tracking AS et
         WHERE n.reference_type = 'evidence' AND et.id = n.reference_id
        """
    )
    op.execute(
        """
        UPDATE notifications AS n
           SET organization_id = t.organization_id
          FROM evidence_collection_tasks AS t
         WHERE n.reference_type = 'task' AND t.id = n.reference_id
        """
    )
    op.execute(
        """
        UPDATE notifications AS n
           SET organization_id = tm.organization_id
          FROM teams AS tm
         WHERE n.reference_type = 'team' AND tm.id = n.reference_id
        """
    )
    op.execute(
        """
        UPDATE notifications AS n
           SET organization_id = r.organization_id
          FROM organization_reconciliation_runs AS r
         WHERE n.reference_type = 'catalog' AND r.id = n.reference_id
        """
    )
    op.execute(
        """
        UPDATE notifications AS n
           SET organization_id = ae.organization_id
          FROM engagement_queries AS eq
          JOIN audit_engagements AS ae ON ae.id = eq.engagement_id
         WHERE n.reference_type = 'engagement_query' AND eq.id = n.reference_id
        """
    )
    # comment: two hops — the comment row names its commentable, and the
    # commentable carries the org. One UPDATE per commentable table.
    op.execute(
        """
        UPDATE notifications AS n
           SET organization_id = sc.organization_id
          FROM comments AS c
          JOIN scoped_controls AS sc ON sc.id = c.commentable_id
         WHERE n.reference_type = 'comment' AND c.id = n.reference_id
           AND c.commentable_type = 'control'
        """
    )
    op.execute(
        """
        UPDATE notifications AS n
           SET organization_id = et.organization_id
          FROM comments AS c
          JOIN evidence_tracking AS et ON et.id = c.commentable_id
         WHERE n.reference_type = 'comment' AND c.id = n.reference_id
           AND c.commentable_type = 'evidence'
        """
    )
    op.execute(
        """
        UPDATE notifications AS n
           SET organization_id = t.organization_id
          FROM comments AS c
          JOIN evidence_collection_tasks AS t ON t.id = c.commentable_id
         WHERE n.reference_type = 'comment' AND c.id = n.reference_id
           AND c.commentable_type = 'task'
        """
    )
    op.execute(
        """
        UPDATE notifications AS n
           SET organization_id = ae.organization_id
          FROM comments AS c
          JOIN engagement_queries AS eq ON eq.id = c.commentable_id
          JOIN audit_engagements AS ae ON ae.id = eq.engagement_id
         WHERE n.reference_type = 'comment' AND c.id = n.reference_id
           AND c.commentable_type = 'engagement_query'
        """
    )

    # Step 3a: rows still NULL reference an entity that was deleted. No
    # tenant can be attributed and their navigation is dead — remove them.
    op.execute("DELETE FROM notifications WHERE organization_id IS NULL")

    # Step 3b: constrain. Doubles as the check on step 2 + 3a — any row the
    # backfill-or-delete missed aborts the migration here, whole.
    op.alter_column('notifications', 'organization_id', nullable=False)
    op.create_foreign_key(
        'fk_notifications_organization',
        'notifications', 'organizations',
        ['organization_id'], ['id'],
        ondelete='CASCADE',
    )

    # The hot read predicate is now (user_id, organization_id, is_read) —
    # the bell's unread count and list both filter all three.
    op.create_index(
        'ix_notifications_user_org_unread',
        'notifications',
        ['user_id', 'organization_id', 'is_read'],
    )


def downgrade() -> None:
    op.drop_index('ix_notifications_user_org_unread', table_name='notifications')
    op.drop_constraint('fk_notifications_organization', 'notifications', type_='foreignkey')
    op.drop_column('notifications', 'organization_id')
