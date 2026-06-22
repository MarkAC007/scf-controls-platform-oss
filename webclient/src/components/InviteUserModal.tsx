import { useState, useEffect } from 'react'
import { getOrgInvites, cancelOrgInvite } from '../data/apiClient'
import type { OrgInviteResponse } from '../data/apiClient'
import { apiClient } from '../data/apiClient'

interface InviteUserModalProps {
  organizationId: string
  onClose: () => void
  onInviteSent: () => void
}

export default function InviteUserModal({ organizationId, onClose, onInviteSent }: InviteUserModalProps) {
  const [email, setEmail] = useState('')
  const [message, setMessage] = useState('')
  const [role, setRole] = useState<'admin' | 'editor' | 'viewer'>('viewer')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [success, setSuccess] = useState(false)

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
      await apiClient.post(`/organizations/${organizationId}/invite`, {
        email: email.trim(),
        role,
        message: message.trim() || null
      })

      setSuccess(true)
      // Refresh pending invites
      try {
        const data = await getOrgInvites(organizationId, 'pending')
        setPendingInvites(data.invites)
      } catch { /* ignore refresh failure */ }

      setTimeout(() => {
        onInviteSent()
      }, 2000)
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

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal-content" onClick={e => e.stopPropagation()}>
        <div className="modal-header">
          <h2>Invite User</h2>
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
            <p>An invitation email has been sent to <strong>{email}</strong> as <strong>{role}</strong></p>
          </div>
        ) : (
          <form onSubmit={handleSubmit}>
            <div className="modal-body">
              <p className="modal-description">
                Send an invitation email to add a new member to your organisation.
                They'll be able to sign in using their Google account.
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
                onClick={onClose}
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
