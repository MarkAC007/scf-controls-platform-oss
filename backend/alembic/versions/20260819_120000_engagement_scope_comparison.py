"""Engagement scope comparison: tagged mapped-set + out-of-scope justification.

Increment 1 of the framework-native audit-engagement design
(docs/superpowers/specs/2026-08-19-audit-engagements-design.md).

- scoped_controls gains ``out_of_scope_justification`` (rationale for exclusion).
- engagement_control_scope is reworked from "only selected controls" to the
  complete framework-mapped set, tagged in_scope / excluded / not_tracked:
    * new ``scf_id`` (keys the row so mapped-but-untracked controls can exist)
    * ``scoped_control_id`` becomes nullable (NULL = not_tracked)
    * new ``scope_status``, ``out_of_scope_justification``, ``source_frameworks``
    * unique constraint moves from (engagement_id, scoped_control_id) to
      (engagement_id, scf_id)

Existing rows predate the feature and were all selected controls, so they are
backfilled as in_scope with scf_id taken from their scoped_controls row.

Revision ID: eng1scopecmp01
Revises: merge0121heads
Create Date: 2026-08-19 12:00:00.000000
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ARRAY

revision = 'eng1scopecmp01'
down_revision = 'merge0121heads'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # --- scoped_controls: exclusion justification -----------------------------
    op.add_column(
        'scoped_controls',
        sa.Column('out_of_scope_justification', sa.Text(), nullable=True),
    )

    # --- engagement_control_scope: new columns (nullable/defaulted for backfill)
    op.add_column(
        'engagement_control_scope',
        sa.Column('scf_id', sa.String(50), nullable=True),
    )
    op.add_column(
        'engagement_control_scope',
        sa.Column('scope_status', sa.String(20), nullable=False, server_default='in_scope'),
    )
    op.add_column(
        'engagement_control_scope',
        sa.Column('out_of_scope_justification', sa.Text(), nullable=True),
    )
    op.add_column(
        'engagement_control_scope',
        sa.Column('source_frameworks', ARRAY(sa.String()), nullable=False, server_default='{}'),
    )

    # Backfill scf_id for existing rows from their scoped_controls row.
    op.execute(
        """
        UPDATE engagement_control_scope ecs
        SET scf_id = sc.scf_id
        FROM scoped_controls sc
        WHERE ecs.scoped_control_id = sc.id
          AND ecs.scf_id IS NULL
        """
    )

    # scf_id is now populated for every existing row -> enforce NOT NULL.
    op.alter_column('engagement_control_scope', 'scf_id', nullable=False)

    # not_tracked rows have no scoped_control_id -> allow NULL.
    op.alter_column('engagement_control_scope', 'scoped_control_id', nullable=True)

    # Swap the uniqueness guarantee to (engagement_id, scf_id).
    op.drop_constraint('uq_engagement_scoped_control', 'engagement_control_scope', type_='unique')
    op.create_unique_constraint(
        'uq_engagement_scf_id',
        'engagement_control_scope',
        ['engagement_id', 'scf_id'],
    )


def downgrade() -> None:
    op.drop_constraint('uq_engagement_scf_id', 'engagement_control_scope', type_='unique')
    op.create_unique_constraint(
        'uq_engagement_scoped_control',
        'engagement_control_scope',
        ['engagement_id', 'scoped_control_id'],
    )
    # Restoring NOT NULL on scoped_control_id would fail if any not_tracked rows
    # exist; drop those first so the downgrade is deterministic.
    op.execute("DELETE FROM engagement_control_scope WHERE scoped_control_id IS NULL")
    op.alter_column('engagement_control_scope', 'scoped_control_id', nullable=False)

    op.drop_column('engagement_control_scope', 'source_frameworks')
    op.drop_column('engagement_control_scope', 'out_of_scope_justification')
    op.drop_column('engagement_control_scope', 'scope_status')
    op.drop_column('engagement_control_scope', 'scf_id')

    op.drop_column('scoped_controls', 'out_of_scope_justification')
