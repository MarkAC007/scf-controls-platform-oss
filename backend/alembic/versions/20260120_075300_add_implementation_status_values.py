"""Add Ready for Review and Monitored implementation status values.

This migration extends the implementation_status field to support SCFConnect-aligned
workflow statuses. The field is a VARCHAR(50), so no schema change is needed.

This migration:
1. Documents the new valid status values
2. Creates an index on implementation_status for faster filtering
3. Is fully reversible with no data loss

New workflow: Not Started -> In Progress -> Implemented -> Ready for Review -> Monitored
(Not Applicable can be set at any time)

Status Values:
- not_started: Control has not begun implementation
- in_progress: Control is actively being implemented
- implemented: Control implementation is complete, awaiting review
- ready_for_review: Control is ready for formal review/assessment
- monitored: Control is in ongoing monitoring state
- not_applicable: Control does not apply to this organisation
- at_risk: Control implementation is at risk (existing value)
- deferred: Control implementation has been deferred (existing value)

Revision ID: a1b2c3d4e5f6
Revises: 62bdef86f249
Create Date: 2026-01-20 07:53:00.000000+00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, None] = '62bdef86f249'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Valid implementation status values after this migration
VALID_STATUSES = [
    'not_started',
    'in_progress',
    'implemented',
    'ready_for_review',  # NEW: Between implemented and monitored
    'monitored',         # NEW: Ongoing monitoring state
    'not_applicable',
    'at_risk',
    'deferred',
]


def upgrade() -> None:
    """Add index on implementation_status for improved query performance.

    The implementation_status column is already VARCHAR(50), so it can accept
    any string value. This migration:
    1. Adds an index for filtering/grouping by status
    2. Documents the expanded set of valid values

    No data transformation needed - existing values remain valid.
    """
    # Add index on implementation_status for faster filtering
    # This is useful for dashboard queries that filter by status
    op.create_index(
        'ix_scoped_controls_implementation_status',
        'scoped_controls',
        ['implementation_status'],
        unique=False
    )

    # Add a CHECK constraint to validate status values (optional but recommended)
    # Note: This uses PostgreSQL-specific syntax
    # We use a loose constraint that allows NULL and any of the valid values
    op.execute("""
        ALTER TABLE scoped_controls
        ADD CONSTRAINT ck_scoped_controls_implementation_status
        CHECK (
            implementation_status IS NULL OR
            implementation_status IN (
                'not_started',
                'in_progress',
                'implemented',
                'ready_for_review',
                'monitored',
                'not_applicable',
                'at_risk',
                'deferred'
            )
        )
    """)


def downgrade() -> None:
    """Remove the index and constraint.

    Note: This does NOT remove any data or change existing values.
    After downgrade, 'ready_for_review' and 'monitored' values would
    become invalid according to the old business logic, but the data
    itself is preserved for manual migration if needed.
    """
    # Remove the CHECK constraint
    op.execute("""
        ALTER TABLE scoped_controls
        DROP CONSTRAINT IF EXISTS ck_scoped_controls_implementation_status
    """)

    # Remove the index
    op.drop_index('ix_scoped_controls_implementation_status', table_name='scoped_controls')
