"""Add webhook_endpoints and webhook_deliveries tables for Evidence Inbox.

Revision ID: x6y7z8a9b0c1
Revises: w5x6y7z8a9b0
Create Date: 2026-02-19 12:00:00.000000

Issue: #214 - Webhook Ingestion API (Evidence Inbox)
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

# revision identifiers, used by Alembic.
revision = 'x6y7z8a9b0c1'
down_revision = 'w5x6y7z8a9b0'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # -- webhook_endpoints --------------------------------------------------
    op.create_table(
        'webhook_endpoints',
        sa.Column('id', UUID(as_uuid=True), primary_key=True, server_default=sa.text('gen_random_uuid()')),
        sa.Column('organization_id', UUID(as_uuid=True), sa.ForeignKey('organizations.id', ondelete='CASCADE'), nullable=False),
        sa.Column('name', sa.String(200), nullable=False),
        sa.Column('description', sa.Text, nullable=True),
        sa.Column('secret', sa.String(70), nullable=False),  # plaintext "whsec_..." needed for HMAC verification
        sa.Column('secret_prefix', sa.String(12), nullable=False),  # first 12 chars for display
        sa.Column('is_active', sa.Boolean, server_default='true', nullable=False),
        sa.Column('allowed_evidence_ids', sa.JSON, nullable=True),  # null = allow any
        sa.Column('created_by_user_id', UUID(as_uuid=True), sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
        sa.Column('last_delivery_at', sa.DateTime(timezone=False), nullable=True),
        sa.Column('delivery_count', sa.Integer, server_default='0', nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=False), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=False), server_default=sa.text('now()'), nullable=False),
    )

    op.create_index('ix_webhook_endpoints_org', 'webhook_endpoints', ['organization_id'])
    op.create_index('ix_webhook_endpoints_org_active', 'webhook_endpoints', ['organization_id', 'is_active'])

    # -- webhook_deliveries -------------------------------------------------
    op.create_table(
        'webhook_deliveries',
        sa.Column('id', UUID(as_uuid=True), primary_key=True, server_default=sa.text('gen_random_uuid()')),
        sa.Column('webhook_endpoint_id', UUID(as_uuid=True), sa.ForeignKey('webhook_endpoints.id', ondelete='CASCADE'), nullable=False),
        sa.Column('organization_id', UUID(as_uuid=True), sa.ForeignKey('organizations.id', ondelete='CASCADE'), nullable=False),
        sa.Column('evidence_id', sa.String(50), nullable=False),
        sa.Column('event_id', sa.String(100), nullable=True),  # sender-provided idempotency key
        sa.Column('payload_json', sa.JSON, nullable=True),
        sa.Column('content_type', sa.String(100), nullable=True),
        sa.Column('signature_valid', sa.Boolean, nullable=False),
        sa.Column('status', sa.String(20), server_default='received', nullable=False),  # received/processed/rejected/failed
        sa.Column('error_message', sa.Text, nullable=True),
        sa.Column('evidence_file_id', UUID(as_uuid=True), sa.ForeignKey('evidence_files.id', ondelete='SET NULL'), nullable=True),
        sa.Column('ip_address', sa.String(45), nullable=True),
        sa.Column('user_agent', sa.String(500), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=False), server_default=sa.text('now()'), nullable=False),
        sa.Column('processed_at', sa.DateTime(timezone=False), nullable=True),
    )

    op.create_index('ix_webhook_deliveries_org_created', 'webhook_deliveries', ['organization_id', sa.text('created_at DESC')])
    op.create_index('ix_webhook_deliveries_endpoint', 'webhook_deliveries', ['webhook_endpoint_id'])
    # Unique partial index on event_id where not null (idempotency)
    op.execute(
        "CREATE UNIQUE INDEX ix_webhook_deliveries_event_id "
        "ON webhook_deliveries (event_id) WHERE event_id IS NOT NULL"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_webhook_deliveries_event_id")
    op.drop_index('ix_webhook_deliveries_endpoint')
    op.drop_index('ix_webhook_deliveries_org_created')
    op.drop_table('webhook_deliveries')

    op.drop_index('ix_webhook_endpoints_org_active')
    op.drop_index('ix_webhook_endpoints_org')
    op.drop_table('webhook_endpoints')
