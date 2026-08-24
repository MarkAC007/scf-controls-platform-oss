"""Per-organisation assurance policy: attestation gate + reviewer independence

Two rules, one record (#787, #803). Both default false, and an *absent* row
reads as both-false — so this migration creates an empty table and changes no
organisation's behaviour. There is deliberately no backfill: a table with no
rows is a stronger no-op guarantee than an INSERT that could miss a tenant.

Revision ID: orgassurance01
Revises: evassertions001
Create Date: 2026-08-23 23:30:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = 'orgassurance01'
down_revision: Union[str, None] = 'evassertions001'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'organization_assurance_policies',
        sa.Column(
            'organization_id',
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey('organizations.id', ondelete='CASCADE'),
            primary_key=True,
            nullable=False,
        ),
        sa.Column(
            'require_evidence_attestation',
            sa.Boolean(),
            nullable=False,
            server_default=sa.text('false'),
        ),
        sa.Column(
            'require_reviewer_independence',
            sa.Boolean(),
            nullable=False,
            server_default=sa.text('false'),
        ),
        sa.Column(
            'created_at',
            sa.DateTime(timezone=False),
            nullable=False,
            server_default=sa.text('now()'),
        ),
        sa.Column(
            'updated_at',
            sa.DateTime(timezone=False),
            nullable=False,
            server_default=sa.text('now()'),
        ),
    )


def downgrade() -> None:
    op.drop_table('organization_assurance_policies')
