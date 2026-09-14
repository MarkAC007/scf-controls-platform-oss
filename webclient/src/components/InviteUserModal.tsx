import { useState, useEffect } from 'react'
import { getOrgInvites, cancelOrgInvite } from '../data/apiClient'
import type { OrgInviteResponse, OrgInviteIdpStatus } from '../data/apiClient'
import { apiClient } from '../data/apiClient'
import { OIDC_ENABLED } from '../data/authToken'
import type { MemberType } from '../types'
import { ContractorBadge } from './ContractorBadge'
import { useModalDismiss } from '../hooks/useModalDismiss'

interface InviteUserModalProps {
  organizationId: string
  onClose: () => void
  onInviteSent: () => void
}

/**
 * Where a pending invitee stands in the identity provider (#984).
 *
 * The list is the only place an admin can see, between sending an invitation
 * and its acceptance, whether the person on the other end can actually sign
 * in. A row without this badge looks identical whether the account exists or
 * not, which is exactly the confusion #984 was reported as.
 *
 * No status (a backend predating #984, or provisioning switched off) renders
 * nothing at all — an unknown is not a finding.
 */
const IDP_BADGES: Record<OrgInviteIdpStatus, { label: string; modifier: string; title: string }> = {
  provisioned: {
    label: 'IdP account',
    modifier: 'idp-badge--provisioned',
    title: 'An identity-provider account exists for this address. They can sign in.',
  },
  not_in_idp: {
    label: 'No IdP account',
    modifier: 'idp-badge--missing',
    title:
      'No identity-provider account exists for this address yet, so they cannot sign in. Cancel and re-invite to create one, or create it in the Keycloak console.',
  },
  external: {
    label: 'External IdP',
    modifier: 'idp-badge--external',
    title:
      'Your own identity provider owns this account. Create it there if it does not exist.',
  },
}

function IdpStatusBadge({ status }: { status?: OrgInviteIdpStatus | null }) {
  if (!status) return null
  const badge = IDP_BADGES[status]
  if (!badge) return null
  return (
    <span className={`idp-badge ${badge.modifier}`} title={badge.title}>
      {badge.label}
    </span>
  )
}

export default function InviteUserModal({ organizationId, onClose, onInviteSent }: InviteUserModalProps) {
  useModalDismiss(true, onClose)

  const [email, setEmail] = useState('')
  const [message, setMessage] = useState('')
  const [role, setRole] = useState<'admin' | 'editor' | 'viewer'>('viewer')
  /**
   * Employment type to record on the membership when the invite is accepted
   * (#822 phase 2).
   *
   * Defaults to 'internal', matching the API's own default and the column's
   * server default, so an admin who ignores this control gets exactly the
   * behaviour they got before it existed.
   *
   * Independent of `role` above and must stay that way: this grants nothing,
   * so inviting somebody as a contractor must never narrow which roles they
   * may be given.
   */
  const [memberType, setMemberType] = useState<MemberType>('internal')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [success, setSuccess] = useState(false)

  /**
   * The one-time IdP password the API issued for this invitee (#984), and what
   * it says about their identity-provider account.
   *
   * Component state and nothing else. Writing it to localStorage, a log line
   * or a query string would defeat the "once" that makes it safe to show at
   * all, so it lives exactly as long as this success state does.
   */
  const [tempPassword, setTempPassword] = useState<string | null>(null)
  const [idpStatus, setIdpStatus] = useState<OrgInviteIdpStatus | null>(null)
  const [passwordCopied, setPasswordCopied] = useState(false)

  // Pending invites
  const [pendingInvites, setPendingInvites] = useState<OrgInviteResponse[]>([])
  const [invitesLoading, setInvitesLoading] = useState(true)
  const [cancellingId, setCancellingId] = useState<string | null>(null)

  // Load pending invites on mount
  useEffect(() => {
    async function loadInvites() {
      try {
        setInvitesLoading(true)
        const data = await getOrgInvites(organizationId, 'pending')
        setPendingInvites(data.invites)
      } catch (err) {
        console.error('Failed to load pending invites:', err)
      } finally {
        setInvitesLoading(false)
      }
    }
    loadInvites()
  }, [organizationId])

  const isValidEmail = (email: string): boolean => {
    return /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)
  }

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()

    if (!email.trim()) {
      setError('Email address is required')
      return
    }

    if (!isValidEmail(email)) {
      setError('Please enter a valid email address')
      return
    }

    setLoading(true)
    setError(null)

    try {
      const created = await apiClient.post<OrgInviteResponse>(
        `/organizations/${organizationId}/invite`,
        {
          email: email.trim(),
          role,
          // A body field, unlike the members PATCH's query parameter — the
          // invite endpoint takes a JSON body and this rides along in it.
          member_type: memberType,
          message: message.trim() || null
        }
      )

      // Absent fields (a backend predating #984) read as "nothing to say".
      const issuedPassword = created?.idp_temporary_password ?? null
      setTempPassword(issuedPassword)
      setIdpStatus(created?.idp_status ?? null)
      setPasswordCopied(false)
      setSuccess(true)
      // Refresh pending invites
      try {
        const data = await getOrgInvites(organizationId, 'pending')
        setPendingInvites(data.invites)
      } catch { /* ignore refresh failure */ }

      // The 2s auto-dismiss is right for an invitation that was emailed and
      // wrong for one carrying a secret shown exactly once: it would take the
      // password off the screen before the admin could copy it. When there is
      // one, the admin dismisses this themselves.
      if (!issuedPassword) {
        setTimeout(() => {
          onInviteSent()
        }, 2000)
      }
    } catch (err: any) {
      console.error('Failed to send invitation:', err)
      const detail = err?.detail
      // Handle 402 subscription limit error
      if (typeof detail === 'object' && detail?.message) {
        setError(detail.message)
      } else {
        setError(err.message || 'Failed to send invitation')
      }
    } finally {
      setLoading(false)
    }
  }

  const handleCancelInvite = async (inviteId: string) => {
    setCancellingId(inviteId)
    try {
      await cancelOrgInvite(organizationId, inviteId)
      setPendingInvites(prev => prev.filter(inv => inv.id !== inviteId))
    } catch (err: any) {
      console.error('Failed to cancel invite:', err)
      setError(err.message || 'Failed to cancel invitation')
    } finally {
      setCancellingId(null)
    }
  }

  /**
   * Drop the secret before handing control back to the parent. The component
   * normally unmounts on close and takes its state with it, but this must not
   * depend on that: a parent that keeps the modal mounted would otherwise be
   * holding a password that has already been "shown once".
   */
  const clearIdpResult = () => {
    setTempPassword(null)
    setIdpStatus(null)
    setPasswordCopied(false)
  }

  const handleClose = () => {
    clearIdpResult()
    onClose()
  }

  const handleDone = () => {
    clearIdpResult()
    onInviteSent()
  }

  const handleInviteAnother = () => {
    clearIdpResult()
    setSuccess(false)
    setEmail('')
    setMessage('')
    setError(null)
  }

  const handleCopyPassword = () => {
    if (!tempPassword) return
    navigator.clipboard.writeText(tempPassword).then(() => {
      setPasswordCopied(true)
      setTimeout(() => setPasswordCopied(false), 2000)
    })
  }

  return (
    <div className="modal-overlay" onClick={handleClose}>
      <div className="modal-content" onClick={e => e.stopPropagation()}>
        <div className="modal-header">
          <h2>Invite User</h2>
          <button className="modal-close" onClick={handleClose} aria-label="Close">
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
            {/*
              "Invitation created", not "an invitation email has been sent":
              this screen cannot know whether the install has email configured,
              and a self-hosted one frequently does not. Promising an email
              that never arrives is how an admin ends up waiting instead of
              passing on the password below.
            */}
            <p>
              Invitation created for <strong>{email}</strong> as{' '}
              <strong>{role}</strong>
              {memberType === 'external_contractor' && <> (contractor)</>}.
            </p>

            {tempPassword && (
              <div className="idp-temp-password">
                <label className="idp-temp-password-label">
                  Temporary password (shown once)
                </label>
                <div className="idp-temp-password-row">
                  <code data-testid="idp-temp-password">{tempPassword}</code>
                  <button
                    type="button"
                    className="btn-copy-temp-password"
                    onClick={handleCopyPassword}
                  >
                    {passwordCopied ? 'Copied' : 'Copy'}
                  </button>
                </div>
                <p className="idp-temp-password-help">
                  Share it with them securely. They must change it at first
                  sign-in. It will not be shown again.
                </p>
              </div>
            )}

            {!tempPassword && idpStatus === 'provisioned' && (
              <p className="idp-status-note">
                They already have an identity-provider account; no password was
                set.
              </p>
            )}

            {!tempPassword && idpStatus === 'external' && (
              <p className="idp-status-note">
                Their account is managed by your identity provider.
              </p>
            )}

            {/*
              Only when a secret is on screen. Without one the modal keeps its
              existing 2s auto-dismiss, so a Google-flow install sees exactly
              the success state it saw before #984.
            */}
            {tempPassword && (
              <div className="modal-footer">
                <button
                  type="button"
                  className="btn-secondary"
                  onClick={handleInviteAnother}
                >
                  Invite another
                </button>
                <button type="button" className="btn-primary" onClick={handleDone}>
                  Done
                </button>
              </div>
            )}
          </div>
        ) : (
          <form onSubmit={handleSubmit}>
            <div className="modal-body">
              {/*
                What happens next differs by install, and the old sentence was
                simply false on one of them (#984). On an OIDC install the
                platform creates the account itself and the password appears on
                the next screen; saying "Google" there sends the admin looking
                for a sign-in method the install does not have.
              */}
              <p className="modal-description">
                Send an invitation email to add a new member to your organisation.{' '}
                {OIDC_ENABLED
                  ? 'An account is created for them in the identity provider and a temporary password is shown here once. They sign in with it and are asked to set their own.'
                  : "They'll be able to sign in using their Google account."}
              </p>

              <div className="form-group">
                <label htmlFor="email">Email Address *</label>
                <input
                  id="email"
                  type="email"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  placeholder="colleague@company.com"
                  disabled={loading}
                  autoFocus
                />
              </div>

              <div className="form-group">
                <label htmlFor="role">Role *</label>
                <select
                  id="role"
                  value={role}
                  onChange={(e) => setRole(e.target.value as 'admin' | 'editor' | 'viewer')}
                  disabled={loading}
                  className="role-select"
                >
                  <option value="viewer">Viewer - Read-only access</option>
                  <option value="editor">Editor - Can edit controls and evidence</option>
                  <option value="admin">Admin - Full management access</option>
                </select>
              </div>

              <div className="form-group">
                <label htmlFor="member-type">Employment Type *</label>
                <select
                  id="member-type"
                  value={memberType}
                  onChange={(e) => setMemberType(e.target.value as MemberType)}
                  disabled={loading}
                  className="member-type-select"
                >
                  <option value="internal">Internal - permanent staff</option>
                  <option value="external_contractor">
                    Contractor - works for you under a contract
                  </option>
                </select>
                <small className="form-text text-muted">
                  A label shown beside this person's name. It grants and
                  restricts nothing — access comes from the role above.
                </small>
              </div>

              <div className="form-group">
                <label htmlFor="message">Personal Message (optional)</label>
                <textarea
                  id="message"
                  value={message}
                  onChange={(e) => setMessage(e.target.value)}
                  placeholder="Add a personal note to your invitation..."
                  rows={3}
                  disabled={loading}
                  maxLength={500}
                />
                <span className="char-count">{message.length}/500</span>
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

              {/* Pending invites section */}
              {pendingInvites.length > 0 && (
                <div className="pending-invites">
                  <h4>Pending Invitations ({pendingInvites.length})</h4>
                  <div className="pending-list">
                    {pendingInvites.map(inv => (
                      <div key={inv.id} className="pending-item">
                        <div className="pending-info">
                          <span className="pending-email">{inv.email}</span>
                          <span className="pending-role">{inv.role}</span>
                          <ContractorBadge
                            memberType={inv.member_type}
                            personName={inv.email}
                          />
                          <IdpStatusBadge status={inv.idp_status} />
                        </div>
                        <button
                          type="button"
                          className="btn-cancel-invite"
                          onClick={() => handleCancelInvite(inv.id)}
                          disabled={cancellingId === inv.id}
                          title="Cancel invitation"
                        >
                          {cancellingId === inv.id ? (
                            <span className="spinner-small" />
                          ) : (
                            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                              <line x1="18" y1="6" x2="6" y2="18" />
                              <line x1="6" y1="6" x2="18" y2="18" />
                            </svg>
                          )}
                        </button>
                      </div>
                    ))}
                  </div>
                </div>
              )}
              {invitesLoading && (
                <div className="pending-loading">
                  <span className="spinner-small" /> Loading pending invites...
                </div>
              )}
            </div>

            <div className="modal-footer">
              <button
                type="button"
                className="btn-secondary"
                onClick={handleClose}
                disabled={loading}
              >
                Cancel
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
        )}
      </div>
    </div>
  )
}
