-- Migration 012: Add organization_invites table
-- Supports the organisation member invitation system with secure tokens,
-- domain enforcement, and role-based invitations.

CREATE TABLE IF NOT EXISTS organization_invites (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    invited_by_user_id UUID REFERENCES users(id) ON DELETE SET NULL,
    email VARCHAR(255) NOT NULL,
    role VARCHAR(50) NOT NULL DEFAULT 'viewer',
    invite_token VARCHAR(64) NOT NULL UNIQUE,
    status VARCHAR(20) NOT NULL DEFAULT 'pending',
    custom_message TEXT,
    expires_at TIMESTAMP NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP NOT NULL DEFAULT NOW()
);

-- Index for looking up invites by org + email + status (duplicate checking)
CREATE INDEX IF NOT EXISTS idx_org_invites_org_email_status
    ON organization_invites(organization_id, email, status);

-- Index for token lookup (accept flow)
CREATE INDEX IF NOT EXISTS idx_org_invites_token
    ON organization_invites(invite_token);

-- Index for listing invites by organisation
CREATE INDEX IF NOT EXISTS idx_org_invites_org_id
    ON organization_invites(organization_id);
