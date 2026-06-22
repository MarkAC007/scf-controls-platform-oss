"""Add Phase 1 Evidence Inbox fields: rate limiting, malware scan, health config.

Revision ID: z8a9b0c1d2e3
Revises: y7z8a9b0c1d2
Create Date: 2026-02-20 12:00:00.000000

Issues: #216 Rate Limiting, #217 Malware Scan, #220 Evidence Health Dashboard
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB

# revision identifiers, used by Alembic.
revision = 'z8a9b0c1d2e3'
down_revision = 'y7z8a9b0c1d2'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # --- #216: Rate limiting per webhook endpoint ---
    op.add_column(
        'webhook_endpoints',
        sa.Column('rate_limit_per_minute', sa.Integer(), nullable=True),
    )

    # --- #217: Malware scan status on evidence files ---
    op.add_column(
        'evidence_files',
        sa.Column('scan_status', sa.String(20), nullable=False, server_default='pending'),
    )
    op.add_column(
        'evidence_files',
        sa.Column('scan_details', JSONB, nullable=True),
    )
    op.create_index('ix_evidence_files_scan_status', 'evidence_files', ['scan_status'])

    # --- #220: Evidence health configuration per org ---
    op.create_table(
        'evidence_health_config',
        sa.Column('id', UUID(as_uuid=True), primary_key=True, server_default=sa.text('gen_random_uuid()')),
        sa.Column('organization_id', UUID(as_uuid=True), sa.ForeignKey('organizations.id', ondelete='CASCADE'), nullable=False),
        sa.Column('evidence_id', sa.String(50), nullable=False),
        sa.Column('staleness_warning_days', sa.Integer(), nullable=False, server_default='30'),
        sa.Column('staleness_critical_days', sa.Integer(), nullable=False, server_default='60'),
        sa.Column('created_at', sa.DateTime(timezone=False), server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=False), server_default=sa.func.now()),
        sa.UniqueConstraint('organization_id', 'evidence_id', name='uq_evidence_health_config_org_evidence'),
    )
    op.create_index('ix_evidence_health_config_org_id', 'evidence_health_config', ['organization_id'])


def downgrade() -> None:
    op.drop_table('evidence_health_config')
    op.drop_index('ix_evidence_files_scan_status', table_name='evidence_files')
    op.drop_column('evidence_files', 'scan_details')
    op.drop_column('evidence_files', 'scan_status')
    op.drop_column('webhook_endpoints', 'rate_limit_per_minute')
