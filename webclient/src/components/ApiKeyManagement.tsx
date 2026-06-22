/**
 * ApiKeyManagement Component — Per-organisation API key CRUD.
 *
 * Admins can create keys (for themselves), see all keys, and revoke any key.
 * Non-admins see only their own keys and can revoke those.
 * The plaintext key is shown once at creation time with a copy button.
 */
import { useState, useEffect, useCallback } from 'react'
import { toast } from 'react-hot-toast'
import {
  getOrgApiKeys,
  createOrgApiKey,
  revokeOrgApiKey,
} from '../data/apiClient'
import type { OrgApiKey, OrgApiKeyCreated } from '../data/apiClient'

interface ApiKeyManagementProps {
  organizationId: string
}

export default function ApiKeyManagement({ organizationId }: ApiKeyManagementProps) {
  const [keys, setKeys] = useState<OrgApiKey[]>([])
  const [loading, setLoading] = useState(true)
  const [showCreateModal, setShowCreateModal] = useState(false)
  const [newKeyName, setNewKeyName] = useState('')
  const [newKeyExpiry, setNewKeyExpiry] = useState('')
  const [creating, setCreating] = useState(false)
  const [createdKey, setCreatedKey] = useState<OrgApiKeyCreated | null>(null)
  const [copied, setCopied] = useState(false)

  const loadKeys = useCallback(async () => {
    try {
      setLoading(true)
      const data = await getOrgApiKeys(organizationId)
      setKeys(data)
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : 'Failed to load API keys'
      toast.error(message)
    } finally {
      setLoading(false)
    }
  }, [organizationId])

  useEffect(() => {
    loadKeys()
  }, [loadKeys])

  const handleCreate = async () => {
    if (!newKeyName.trim()) {
      toast.error('Key name is required')
      return
    }
    try {
      setCreating(true)
      const result = await createOrgApiKey(
        organizationId,
        newKeyName.trim(),
        newKeyExpiry || undefined
      )
      setCreatedKey(result)
      toast.success('API key created')
      await loadKeys()
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : 'Failed to create API key'
      toast.error(message)
    } finally {
      setCreating(false)
    }
  }

  const handleRevoke = async (keyId: string, keyName: string) => {
    if (!confirm(`Revoke API key "${keyName}"? This cannot be undone.`)) return
    try {
      await revokeOrgApiKey(organizationId, keyId)
      toast.success('API key revoked')
      await loadKeys()
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : 'Failed to revoke API key'
      toast.error(message)
    }
  }

  const handleCopy = async (text: string) => {
    await navigator.clipboard.writeText(text)
    setCopied(true)
    setTimeout(() => setCopied(false), 2000)
  }

  const closeCreateModal = () => {
    setShowCreateModal(false)
    setNewKeyName('')
    setNewKeyExpiry('')
    setCreatedKey(null)
    setCopied(false)
  }

  const roleBadgeClass = (role: string) => {
    switch (role) {
      case 'admin': return 'badge badge-admin'
      case 'editor': return 'badge badge-editor'
      default: return 'badge badge-viewer'
    }
  }

  const formatDate = (iso: string | null) => {
    if (!iso) return '—'
    return new Date(iso).toLocaleDateString('en-GB', {
      day: 'numeric', month: 'short', year: 'numeric',
    })
  }

  return (
    <div className="api-keys-section">
      <div className="section-header" style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '24px' }}>
        <div>
          <h3>API Keys</h3>
          <p>
            Create scoped API keys for programmatic access to this organisation.
          </p>
        </div>
        <button
          className="btn btn-primary"
          onClick={() => setShowCreateModal(true)}
          style={{ whiteSpace: 'nowrap' }}
        >
          + Create Key
        </button>
      </div>

      {loading ? (
        <div style={{ textAlign: 'center', padding: '2rem' }}>
          <div className="loading-spinner" />
        </div>
      ) : keys.length === 0 ? (
        <div className="api-keys-table-container" style={{ textAlign: 'center', padding: '2rem', color: 'var(--muted)' }}>
          No API keys yet. Create one to get started.
        </div>
      ) : (
        <div className="api-keys-table-container">
          <table className="api-key-table">
            <thead>
              <tr>
                <th>Name</th>
                <th>Key Prefix</th>
                <th>Role</th>
                <th>Created By</th>
                <th>Last Used</th>
                <th>Expires</th>
                <th>Status</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {keys.map(k => (
                <tr key={k.id} style={{ opacity: k.is_active ? 1 : 0.5 }}>
                  <td>{k.name}</td>
                  <td><code className="api-key-prefix">{k.key_prefix}...</code></td>
                  <td><span className={roleBadgeClass(k.role)}>{k.role}</span></td>
                  <td>{k.user_email || '—'}</td>
                  <td>{formatDate(k.last_used_at)}</td>
                  <td>{formatDate(k.expires_at)}</td>
                  <td>
                    {k.is_active ? (
                      <span className="badge badge-active">Active</span>
                    ) : (
                      <span className="badge badge-revoked">Revoked</span>
                    )}
                  </td>
                  <td>
                    {k.is_active && (
                      <button
                        className="btn btn-danger btn-sm"
                        onClick={() => handleRevoke(k.id, k.name)}
                      >
                        Revoke
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* Create Modal */}
      {showCreateModal && (
        <div className="modal-overlay" onClick={closeCreateModal}>
          <div className="modal-content" onClick={e => e.stopPropagation()}>
            <div className="modal-header">
              <h2>{createdKey ? 'API Key Created' : 'Create API Key'}</h2>
              <button className="modal-close" onClick={closeCreateModal} aria-label="Close">
                <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                  <line x1="18" y1="6" x2="6" y2="18" />
                  <line x1="6" y1="6" x2="18" y2="18" />
                </svg>
              </button>
            </div>

            {!createdKey ? (
              <>
                <div className="modal-body">
                  <p className="modal-description">
                    The key will inherit your current role in this organisation.
                  </p>

                  <div className="form-group">
                    <label htmlFor="key-name">Key Name *</label>
                    <input
                      id="key-name"
                      type="text"
                      placeholder="e.g. CI Pipeline, Reporting Script"
                      value={newKeyName}
                      onChange={e => setNewKeyName(e.target.value)}
                      autoFocus
                    />
                  </div>

                  <div className="form-group">
                    <label htmlFor="key-expiry">Expiry Date (optional)</label>
                    <input
                      id="key-expiry"
                      type="date"
                      value={newKeyExpiry}
                      onChange={e => setNewKeyExpiry(e.target.value)}
                    />
                  </div>
                </div>

                <div className="modal-footer">
                  <button className="btn-secondary" onClick={closeCreateModal}>
                    Cancel
                  </button>
                  <button
                    className="btn-primary"
                    onClick={handleCreate}
                    disabled={creating || !newKeyName.trim()}
                  >
                    {creating ? (
                      <>
                        <span className="spinner" />
                        Creating...
                      </>
                    ) : (
                      <>
                        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                          <path d="M21 2l-2 2m-7.61 7.61a5.5 5.5 0 1 1-7.778 7.778 5.5 5.5 0 0 1 7.777-7.777zm0 0L15.5 7.5m0 0l3 3L22 7l-3-3m-3.5 3.5L19 4" />
                        </svg>
                        Create Key
                      </>
                    )}
                  </button>
                </div>
              </>
            ) : (
              <>
                <div className="modal-body">
                  <div className="api-key-created-warning">
                    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                      <path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z" />
                      <line x1="12" y1="9" x2="12" y2="13" />
                      <line x1="12" y1="17" x2="12.01" y2="17" />
                    </svg>
                    Copy this key now. It will not be shown again.
                  </div>

                  <div className="api-key-secret-box">
                    {createdKey.plaintext_key}
                  </div>

                  <button
                    className={`api-key-copy-btn ${copied ? 'copied' : ''}`}
                    onClick={() => handleCopy(createdKey.plaintext_key)}
                  >
                    {copied ? (
                      <>
                        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                          <path d="M20 6L9 17l-5-5" />
                        </svg>
                        Copied!
                      </>
                    ) : (
                      <>
                        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                          <rect x="9" y="9" width="13" height="13" rx="2" ry="2" />
                          <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1" />
                        </svg>
                        Copy to Clipboard
                      </>
                    )}
                  </button>
                </div>

                <div className="modal-footer">
                  <button className="btn-primary" onClick={closeCreateModal}>
                    Done
                  </button>
                </div>
              </>
            )}
          </div>
        </div>
      )}
    </div>
  )
}
