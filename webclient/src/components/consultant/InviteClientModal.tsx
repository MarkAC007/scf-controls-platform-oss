import { useState } from 'react'
import type { ConsultantInvite } from '../../types'

type ModalStep = 'create_org' | 'invite_admin'

interface InviteClientModalProps {
  pendingInvites?: ConsultantInvite[]
  onClose: () => void
  onSubmit: (email: string, orgName: string) => Promise<void>
  onCreateOrg?: (orgName: string) => Promise<{ id: string; name: string }>
  onInviteAdmin?: (orgId: string, email: string) => Promise<void>
  onCancelInvite?: (inviteId: string) => void
}

export default function InviteClientModal({
  pendingInvites = [],
  onClose,
  onSubmit,
  onCreateOrg,
  onInviteAdmin,
  onCancelInvite
}: InviteClientModalProps) {
  const [email, setEmail] = useState('')
  const [orgName, setOrgName] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [success, setSuccess] = useState(false)

  // Two-step flow state
  const useTwoStep = !!onCreateOrg && !!onInviteAdmin
  const [step, setStep] = useState<ModalStep>('create_org')
  const [createdOrg, setCreatedOrg] = useState<{ id: string; name: string } | null>(null)

  const isValidEmail = (email: string): boolean => {
    return /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)
  }

  const handleCreateOrg = async (e: React.FormEvent) => {
    e.preventDefault()
    setError(null)

    if (!orgName.trim()) {
      setError('Organisation name is required')
      return
    }

    setLoading(true)
    try {
      const org = await onCreateOrg!(orgName.trim())
      setCreatedOrg(org)
      setStep('invite_admin')
    } catch (err: any) {
      setError(err.message || 'Failed to create organisation')
    } finally {
      setLoading(false)
    }
  }

  const handleInviteAdmin = async (e: React.FormEvent) => {
    e.preventDefault()
    setError(null)

    if (!email.trim()) {
      setError('Email address is required')
      return
    }

    if (!isValidEmail(email)) {
      setError('Please enter a valid email address')
      return
    }

    if (!createdOrg) {
      setError('No organisation created yet')
      return
    }

    setLoading(true)
    try {
      await onInviteAdmin!(createdOrg.id, email.trim())
      setSuccess(true)
      setTimeout(() => {
        onClose()
      }, 2000)
    } catch (err: any) {
      setError(err.message || 'Failed to send invitation')
    } finally {
      setLoading(false)
    }
  }

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    setError(null)

    if (!email.trim()) {
      setError('Email address is required')
      return
    }

    if (!isValidEmail(email)) {
      setError('Please enter a valid email address')
      return
    }

    if (!orgName.trim()) {
      setError('Organisation name is required')
      return
    }

    setLoading(true)

    try {
      await onSubmit(email.trim(), orgName.trim())
      setSuccess(true)
      setTimeout(() => {
        onClose()
      }, 2000)
    } catch (err: any) {
      setError(err.message || 'Failed to send invitation')
    } finally {
      setLoading(false)
    }
  }

  const formatDate = (dateString: string): string => {
    return new Date(dateString).toLocaleDateString('en-GB', {
      day: 'numeric',
      month: 'short',
      year: 'numeric'
    })
  }

  const getStatusBadgeClass = (status: string): string => {
    switch (status) {
      case 'pending': return 'status-pending'
      case 'accepted': return 'status-accepted'
      case 'expired': return 'status-expired'
      case 'cancelled': return 'status-cancelled'
      default: return ''
    }
  }

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal-content invite-client-modal" onClick={e => e.stopPropagation()}>
        <div className="modal-header">
          <h2>Invite Client</h2>
          <button className="modal-close" onClick={onClose} aria-label="Close">
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <line x1="18" y1="6" x2="6" y2="18" />
              <line x1="6" y1="6" x2="18" y2="18" />
            </svg>
          </button>
        </div>

        {success ? (
          <div className="modal-body success-state">
            <div className="success-icon">
              <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="#10b981" strokeWidth="2">
                <circle cx="12" cy="12" r="10" />
                <path d="M9 12l2 2 4-4" />
              </svg>
            </div>
            <h3>Invitation Sent!</h3>
            <p>
              An invitation has been sent to <strong>{email}</strong> for{' '}
              <strong>{createdOrg?.name || orgName}</strong>
            </p>
          </div>
        ) : useTwoStep && step === 'create_org' ? (
          <>
            <form onSubmit={handleCreateOrg}>
              <div className="modal-body">
                <p className="modal-description">
                  Step 1 of 2: Create the client organisation. You will then invite an admin user.
                </p>

                <div className="form-group">
                  <label htmlFor="orgName">Organisation Name *</label>
                  <input
                    id="orgName"
                    type="text"
                    value={orgName}
                    onChange={(e) => setOrgName(e.target.value)}
                    placeholder="e.g., Acme Corporation"
                    disabled={loading}
                    autoFocus
                  />
                </div>

                {error && (
                  <div className="error-message">
                    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                      <circle cx="12" cy="12" r="10" />
                      <line x1="12" y1="8" x2="12" y2="12" />
                      <line x1="12" y1="16" x2="12.01" y2="16" />
                    </svg>
                    {error}
                  </div>
                )}
              </div>

              <div className="modal-footer">
                <button
                  type="button"
                  className="btn-secondary"
                  onClick={onClose}
                  disabled={loading}
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  className="btn-primary"
                  disabled={loading || !orgName.trim()}
                >
                  {loading ? (
                    <>
                      <span className="spinner" />
                      Creating...
                    </>
                  ) : (
                    'Create Organisation'
                  )}
                </button>
              </div>
            </form>
          </>
        ) : useTwoStep && step === 'invite_admin' ? (
          <>
            <form onSubmit={handleInviteAdmin}>
              <div className="modal-body">
                <p className="modal-description">
                  Step 2 of 2: Invite an admin for <strong>{createdOrg?.name}</strong>. They will receive an email to join the organisation.
                </p>

                <div className="form-group">
                  <label htmlFor="email">Admin Email *</label>
                  <input
                    id="email"
                    type="email"
                    value={email}
                    onChange={(e) => setEmail(e.target.value)}
                    placeholder="admin@client-company.com"
                    disabled={loading}
                    autoFocus
                  />
                  <span className="form-hint">
                    This person will be the primary admin for the organisation
                  </span>
                </div>

                {error && (
                  <div className="error-message">
                    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                      <circle cx="12" cy="12" r="10" />
                      <line x1="12" y1="8" x2="12" y2="12" />
                      <line x1="12" y1="16" x2="12.01" y2="16" />
                    </svg>
                    {error}
                  </div>
                )}
              </div>

              <div className="modal-footer">
                <button
                  type="button"
                  className="btn-secondary"
                  onClick={() => { setStep('create_org'); setError(null) }}
                  disabled={loading}
                >
                  Back
                </button>
                <button
                  type="submit"
                  className="btn-primary"
                  disabled={loading || !email.trim()}
                >
                  {loading ? (
                    <>
                      <span className="spinner" />
                      Sending...
                    </>
                  ) : (
                    <>
                      <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                        <line x1="22" y1="2" x2="11" y2="13" />
                        <polygon points="22 2 15 22 11 13 2 9 22 2" />
                      </svg>
                      Send Invitation
                    </>
                  )}
                </button>
              </div>
            </form>
          </>
        ) : (
          <>
            <form onSubmit={handleSubmit}>
              <div className="modal-body">
                <p className="modal-description">
                  Invite a client to join your consultancy. They will receive an email
                  with instructions to join the organisation.
                </p>

                <div className="form-group">
                  <label htmlFor="orgName">Organisation Name *</label>
                  <input
                    id="orgName"
                    type="text"
                    value={orgName}
                    onChange={(e) => setOrgName(e.target.value)}
                    placeholder="e.g., Acme Corporation"
                    disabled={loading}
                    autoFocus
                  />
                </div>

                <div className="form-group">
                  <label htmlFor="email">Client Email *</label>
                  <input
                    id="email"
                    type="email"
                    value={email}
                    onChange={(e) => setEmail(e.target.value)}
                    placeholder="contact@client-company.com"
                    disabled={loading}
                  />
                  <span className="form-hint">
                    This person will be the primary admin for the organisation
                  </span>
                </div>

                {error && (
                  <div className="error-message">
                    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                      <circle cx="12" cy="12" r="10" />
                      <line x1="12" y1="8" x2="12" y2="12" />
                      <line x1="12" y1="16" x2="12.01" y2="16" />
                    </svg>
                    {error}
                  </div>
                )}
              </div>

              <div className="modal-footer">
                <button
                  type="button"
                  className="btn-secondary"
                  onClick={onClose}
                  disabled={loading}
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  className="btn-primary"
                  disabled={loading || !email.trim() || !orgName.trim()}
                >
                  {loading ? (
                    <>
                      <span className="spinner" />
                      Sending...
                    </>
                  ) : (
                    <>
                      <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                        <line x1="22" y1="2" x2="11" y2="13" />
                        <polygon points="22 2 15 22 11 13 2 9 22 2" />
                      </svg>
                      Send Invitation
                    </>
                  )}
                </button>
              </div>
            </form>

            {/* Pending Invites Section */}
            {pendingInvites.length > 0 && (
              <div className="pending-invites-section">
                <div className="pending-invites-header">
                  <h3>Pending Invitations</h3>
                  <span className="pending-count">{pendingInvites.length}</span>
                </div>
                <div className="pending-invites-list">
                  {pendingInvites.map(invite => (
                    <div key={invite.id} className="pending-invite-item">
                      <div className="invite-info">
                        <div className="invite-org">{invite.organization_name}</div>
                        <div className="invite-email">{invite.email}</div>
                        <div className="invite-meta">
                          <span>Sent {formatDate(invite.created_at)}</span>
                          <span className="invite-separator">|</span>
                          <span>Expires {formatDate(invite.expires_at)}</span>
                        </div>
                      </div>
                      <div className="invite-actions">
                        <span className={`invite-status ${getStatusBadgeClass(invite.status)}`}>
                          {invite.status}
                        </span>
                        {invite.status === 'pending' && onCancelInvite && (
                          <button
                            className="btn-cancel-invite"
                            onClick={() => onCancelInvite(invite.id)}
                            title="Cancel invitation"
                          >
                            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                              <line x1="18" y1="6" x2="6" y2="18" />
                              <line x1="6" y1="6" x2="18" y2="18" />
                            </svg>
                          </button>
                        )}
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </>
        )}
      </div>
    </div>
  )
}
