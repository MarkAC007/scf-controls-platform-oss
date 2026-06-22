/**
 * InviteAcceptance - Component for viewing and accepting invitations
 *
 * Supports two invite types:
 * - "consultant" (default): Consultant invitations that create a new org
 * - "org": Organisation member invitations that join an existing org
 *
 * This component handles the invite acceptance flow:
 * 1. Displays invite details (org name, inviter info)
 * 2. Shows sign-in prompt if not authenticated
 * 3. Allows accepting the invite once authenticated
 * 4. Redirects to the organisation dashboard on success
 *
 * CRITICAL: After acceptance, sets the new org as current in OrganizationContext
 * to ensure user immediately sees the correct organisation.
 */
import { useState, useEffect } from 'react'
import { toast } from 'react-hot-toast'
import { useAuth } from '../contexts/AuthContext'
import { useOrganization } from '../contexts/OrganizationContext'
import {
  getInvitePreview,
  acceptConsultantInvite,
  getOrgInvitePreview,
  acceptOrgInvite,
  type InvitePreviewResponse,
  type OrgInvitePreviewResponse,
} from '../data/apiClient'
import GoogleSignIn from './GoogleSignIn'

type InviteType = 'consultant' | 'org'

interface InviteAcceptanceProps {
  token: string
  inviteType?: InviteType
  onComplete: () => void
  onCancel: () => void
}

export default function InviteAcceptance({ token, inviteType = 'consultant', onComplete, onCancel }: InviteAcceptanceProps) {
  const { isAuthenticated, authReady } = useAuth()
  const { setCurrentOrgId, refreshOrganizations } = useOrganization()
  const [loading, setLoading] = useState(true)
  const [accepting, setAccepting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [invite, setInvite] = useState<InvitePreviewResponse | null>(null)
  const [orgInvite, setOrgInvite] = useState<OrgInvitePreviewResponse | null>(null)

  const isOrgInvite = inviteType === 'org'

  // Load invite details on mount
  useEffect(() => {
    async function loadInvite() {
      try {
        setLoading(true)
        setError(null)
        if (isOrgInvite) {
          const data = await getOrgInvitePreview(token)
          setOrgInvite(data)
        } else {
          const data = await getInvitePreview(token)
          setInvite(data)
        }
      } catch (err: any) {
        console.error('Failed to load invite:', err)
        setError(err.message || 'Failed to load invitation details')
      } finally {
        setLoading(false)
      }
    }

    loadInvite()
  }, [token, isOrgInvite])

  // Track if we've already accepted to prevent double-processing
  const [accepted, setAccepted] = useState(false)

  // Unified invite data for rendering
  const inviteData = isOrgInvite ? orgInvite : invite
  const organizationName = isOrgInvite ? orgInvite?.organization_name : invite?.organization_name
  const inviterLabel = isOrgInvite
    ? (orgInvite?.inviter_name || 'A team member')
    : (invite?.consultant_name || 'A consultant')
  const inviterEmail = isOrgInvite ? orgInvite?.inviter_email : invite?.consultant_email
  const inviteRole = isOrgInvite ? orgInvite?.role : null
  const inviteStatus = inviteData?.status
  const inviteIsExpired = inviteData?.is_expired
  const inviteExpiresAt = inviteData?.expires_at

  // Handle accepting the invite
  const handleAccept = async () => {
    if (!isAuthenticated) {
      toast.error('Please sign in first to accept the invitation')
      return
    }

    try {
      setAccepting(true)
      const result = isOrgInvite
        ? await acceptOrgInvite(token)
        : await acceptConsultantInvite(token)

      // Mark as accepted to prevent showing "already accepted" on re-render
      setAccepted(true)

      // CRITICAL: Set the organisation as current BEFORE redirect
      setCurrentOrgId(result.organization.id)
      console.log(`New organisation set as current: ${result.organization.name} (${result.organization.id})`)

      // Refresh organisations to include the new/joined one in the list
      await refreshOrganizations()

      const successMsg = `You have joined "${result.organization.name}"!`
      toast.success(successMsg)

      // Redirect to dashboard
      onComplete()
    } catch (err: any) {
      console.error('Failed to accept invite:', err)
      toast.error(err.message || 'Failed to accept invitation')
      setAccepting(false)
    }
  }

  // Loading state
  if (loading) {
    return (
      <div className="invite-acceptance">
        <div className="invite-card">
          <div className="loading-spinner" />
          <p>Loading invitation details...</p>
        </div>
      </div>
    )
  }

  // Error state
  if (error || !inviteData) {
    return (
      <div className="invite-acceptance">
        <div className="invite-card invite-error">
          <div className="invite-icon error">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <circle cx="12" cy="12" r="10" />
              <line x1="12" y1="8" x2="12" y2="12" />
              <line x1="12" y1="16" x2="12.01" y2="16" />
            </svg>
          </div>
          <h2>Invalid Invitation</h2>
          <p className="error-message">{error || 'This invitation link is invalid or has expired.'}</p>
          <button onClick={onCancel} className="btn-secondary">
            Go to Home
          </button>
        </div>
      </div>
    )
  }

  // Expired or already used - but skip if WE just accepted it
  if (!accepted && (inviteIsExpired || inviteStatus !== 'pending')) {
    const cancelledBy = isOrgInvite ? 'the organisation admin' : 'the consultant'
    const statusMessages: Record<string, string> = {
      expired: 'This invitation has expired.',
      accepted: 'This invitation has already been accepted.',
      cancelled: `This invitation has been cancelled by ${cancelledBy}.`,
      pending: 'This invitation is no longer valid.'
    }
    const statusMessage = statusMessages[inviteStatus || ''] || 'This invitation is no longer valid.'

    return (
      <div className="invite-acceptance">
        <div className="invite-card invite-expired">
          <div className="invite-icon warning">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z" />
              <line x1="12" y1="9" x2="12" y2="13" />
              <line x1="12" y1="17" x2="12.01" y2="17" />
            </svg>
          </div>
          <h2>Invitation Unavailable</h2>
          <p>{statusMessage}</p>
          <p className="invite-org-name">{organizationName}</p>
          <button onClick={onCancel} className="btn-secondary">
            Go to Home
          </button>
        </div>
      </div>
    )
  }

  // Format expiry date
  const expiryDate = inviteExpiresAt ? new Date(inviteExpiresAt) : null
  const formattedExpiry = expiryDate
    ? expiryDate.toLocaleDateString('en-GB', {
        day: 'numeric',
        month: 'long',
        year: 'numeric',
        hour: '2-digit',
        minute: '2-digit'
      })
    : 'Unknown'

  const description = isOrgInvite
    ? `By accepting this invitation, you'll join "${organizationName}" as a ${inviteRole || 'member'}. You'll be able to collaborate on the organisation's compliance programme.`
    : `By accepting this invitation, you'll join "${organizationName}" as the administrator. The consultant will have access to help manage your compliance programme.`

  const acceptingText = 'Joining organisation...'

  // Valid invite - show acceptance UI
  return (
    <div className="invite-acceptance">
      <div className="invite-card">
        <div className="invite-icon success">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <path d="M22 11.08V12a10 10 0 1 1-5.93-9.14" />
            <polyline points="22 4 12 14.01 9 11.01" />
          </svg>
        </div>

        <h2>You've Been Invited!</h2>

        <div className="invite-details">
          <div className="invite-org">
            <label>Organisation</label>
            <span className="org-name">{organizationName}</span>
          </div>

          <div className="invite-consultant">
            <label>Invited by</label>
            <span className="consultant-info">
              {inviterLabel}
              {inviterEmail && (
                <span className="consultant-email">({inviterEmail})</span>
              )}
            </span>
          </div>

          {inviteRole && (
            <div className="invite-role">
              <label>Role</label>
              <span className="role-badge">{inviteRole}</span>
            </div>
          )}

          <div className="invite-expiry">
            <label>Expires</label>
            <span>{formattedExpiry}</span>
          </div>
        </div>

        <p className="invite-description">
          {description}
        </p>

        {!authReady ? (
          <div className="invite-loading">
            <div className="loading-spinner small" />
            <p>Checking authentication...</p>
          </div>
        ) : !isAuthenticated ? (
          <div className="invite-signin">
            <p className="signin-prompt">
              Sign in with Google to accept this invitation:
            </p>
            <GoogleSignIn />
          </div>
        ) : (
          <div className="invite-actions">
            <button
              onClick={handleAccept}
              disabled={accepting}
              className="btn-primary btn-large"
            >
              {accepting ? (
                <>
                  <span className="loading-spinner small" />
                  {acceptingText}
                </>
              ) : (
                'Accept Invitation'
              )}
            </button>
            <button
              onClick={onCancel}
              disabled={accepting}
              className="btn-secondary"
            >
              Decline
            </button>
          </div>
        )}
      </div>

      <style>{`
        .invite-acceptance {
          min-height: 100vh;
          display: flex;
          align-items: center;
          justify-content: center;
          padding: 2rem;
          background: linear-gradient(135deg, #1e293b 0%, #0f172a 100%);
        }

        .invite-card {
          background: var(--color-surface, #1e293b);
          border-radius: 16px;
          padding: 2.5rem;
          max-width: 480px;
          width: 100%;
          text-align: center;
          box-shadow: 0 25px 50px -12px rgba(0, 0, 0, 0.25);
          border: 1px solid var(--color-border, #334155);
        }

        .invite-icon {
          width: 64px;
          height: 64px;
          margin: 0 auto 1.5rem;
          border-radius: 50%;
          display: flex;
          align-items: center;
          justify-content: center;
        }

        .invite-icon svg {
          width: 32px;
          height: 32px;
        }

        .invite-icon.success {
          background: rgba(74, 222, 128, 0.1);
          color: #4ade80;
        }

        .invite-icon.warning {
          background: rgba(251, 191, 36, 0.1);
          color: #fbbf24;
        }

        .invite-icon.error {
          background: rgba(239, 68, 68, 0.1);
          color: #ef4444;
        }

        .invite-card h2 {
          font-size: 1.5rem;
          font-weight: 600;
          margin-bottom: 1.5rem;
          color: var(--color-text, #f1f5f9);
        }

        .invite-details {
          background: var(--color-background, #0f172a);
          border-radius: 12px;
          padding: 1.25rem;
          margin-bottom: 1.5rem;
          text-align: left;
        }

        .invite-details > div {
          margin-bottom: 1rem;
        }

        .invite-details > div:last-child {
          margin-bottom: 0;
        }

        .invite-details label {
          display: block;
          font-size: 0.75rem;
          font-weight: 500;
          text-transform: uppercase;
          letter-spacing: 0.05em;
          color: var(--color-text-secondary, #94a3b8);
          margin-bottom: 0.25rem;
        }

        .invite-org .org-name {
          font-size: 1.25rem;
          font-weight: 600;
          color: var(--color-primary, #60a5fa);
        }

        .consultant-info {
          color: var(--color-text, #f1f5f9);
        }

        .consultant-email {
          color: var(--color-text-secondary, #94a3b8);
          font-size: 0.875rem;
          margin-left: 0.5rem;
        }

        .role-badge {
          display: inline-block;
          padding: 0.25rem 0.75rem;
          border-radius: 9999px;
          font-size: 0.875rem;
          font-weight: 500;
          text-transform: capitalize;
          background: rgba(59, 130, 246, 0.1);
          color: #60a5fa;
        }

        .invite-description {
          color: var(--color-text-secondary, #94a3b8);
          font-size: 0.875rem;
          line-height: 1.6;
          margin-bottom: 1.5rem;
        }

        .invite-signin {
          margin-top: 1.5rem;
        }

        .signin-prompt {
          color: var(--color-text-secondary, #94a3b8);
          margin-bottom: 1rem;
        }

        .invite-actions {
          display: flex;
          flex-direction: column;
          gap: 0.75rem;
        }

        .btn-primary {
          background: var(--color-primary, #3b82f6);
          color: white;
          border: none;
          padding: 0.875rem 1.5rem;
          border-radius: 8px;
          font-weight: 500;
          cursor: pointer;
          transition: all 0.2s;
          display: flex;
          align-items: center;
          justify-content: center;
          gap: 0.5rem;
        }

        .btn-primary:hover:not(:disabled) {
          background: var(--color-primary-hover, #2563eb);
        }

        .btn-primary:disabled {
          opacity: 0.6;
          cursor: not-allowed;
        }

        .btn-primary.btn-large {
          padding: 1rem 2rem;
          font-size: 1rem;
        }

        .btn-secondary {
          background: transparent;
          color: var(--color-text-secondary, #94a3b8);
          border: 1px solid var(--color-border, #334155);
          padding: 0.75rem 1.5rem;
          border-radius: 8px;
          cursor: pointer;
          transition: all 0.2s;
        }

        .btn-secondary:hover:not(:disabled) {
          background: var(--color-surface-hover, #334155);
          color: var(--color-text, #f1f5f9);
        }

        .error-message {
          color: #ef4444;
          margin-bottom: 1.5rem;
        }

        .invite-org-name {
          color: var(--color-text-secondary, #94a3b8);
          font-size: 0.875rem;
          margin-bottom: 1.5rem;
        }

        .invite-loading,
        .invite-card > p {
          color: var(--color-text-secondary, #94a3b8);
        }

        .loading-spinner.small {
          width: 20px;
          height: 20px;
        }
      `}</style>
    </div>
  )
}
