/**
 * WebhookManagement Component — Per-organisation webhook endpoint CRUD.
 *
 * Admins can view all webhook endpoints, rotate signing secrets, revoke
 * endpoints, and view delivery logs. The "Create" button links to the
 * Collection Wizard which handles endpoint creation.
 *
 * Modelled on ApiKeyManagement.tsx pattern.
 */
import { useState, useEffect, useCallback } from 'react'
import { toast } from 'react-hot-toast'
import {
  listWebhookEndpoints,
  rotateWebhookSecret,
  revokeWebhookEndpoint,
  getWebhookDeliveries,
} from '../data/apiClient'
import type {
  WebhookEndpointResponse,
  WebhookEndpointCreatedResponse,
  WebhookDelivery,
} from '../data/apiClient'

interface WebhookManagementProps {
  organizationId: string
}

export default function WebhookManagement({ organizationId }: WebhookManagementProps) {
  const [endpoints, setEndpoints] = useState<WebhookEndpointResponse[]>([])
  const [loading, setLoading] = useState(true)
  const [expandedId, setExpandedId] = useState<string | null>(null)

  // Rotate secret state
  const [rotateConfirm, setRotateConfirm] = useState<string | null>(null) // endpointId
  const [rotating, setRotating] = useState(false)
  const [rotatedSecret, setRotatedSecret] = useState<WebhookEndpointCreatedResponse | null>(null)
  const [secretCopied, setSecretCopied] = useState(false)

  // Revoke state
  const [revokeConfirm, setRevokeConfirm] = useState<WebhookEndpointResponse | null>(null)
  const [revoking, setRevoking] = useState(false)

  // Delivery logs state
  const [deliveryEndpointId, setDeliveryEndpointId] = useState<string | null>(null)
  const [deliveries, setDeliveries] = useState<WebhookDelivery[]>([])
  const [deliveriesLoading, setDeliveriesLoading] = useState(false)

  const loadEndpoints = useCallback(async () => {
    try {
      setLoading(true)
      const data = await listWebhookEndpoints(organizationId)
      setEndpoints(data)
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : 'Failed to load webhook endpoints'
      toast.error(message)
    } finally {
      setLoading(false)
    }
  }, [organizationId])

  useEffect(() => {
    loadEndpoints()
  }, [loadEndpoints])

  const handleRotateConfirm = async () => {
    if (!rotateConfirm) return
    try {
      setRotating(true)
      const result = await rotateWebhookSecret(organizationId, rotateConfirm)
      setRotatedSecret(result)
      toast.success('Signing secret rotated')
      await loadEndpoints()
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : 'Failed to rotate secret'
      toast.error(message)
    } finally {
      setRotating(false)
    }
  }

  const handleRevokeConfirm = async () => {
    if (!revokeConfirm) return
    try {
      setRevoking(true)
      await revokeWebhookEndpoint(organizationId, revokeConfirm.id)
      toast.success(`Webhook endpoint "${revokeConfirm.name}" revoked`)
      setRevokeConfirm(null)
      await loadEndpoints()
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : 'Failed to revoke endpoint'
      toast.error(message)
    } finally {
      setRevoking(false)
    }
  }

  const handleViewDeliveries = async (endpointId: string) => {
    setDeliveryEndpointId(endpointId)
    setDeliveries([])
    setDeliveriesLoading(true)
    try {
      const result = await getWebhookDeliveries(organizationId, endpointId)
      setDeliveries(result.deliveries)
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : 'Failed to load delivery logs'
      toast.error(message)
    } finally {
      setDeliveriesLoading(false)
    }
  }

  const handleCopySecret = async (secret: string) => {
    await navigator.clipboard.writeText(secret)
    setSecretCopied(true)
    setTimeout(() => setSecretCopied(false), 2000)
  }

  const closeRotateModal = () => {
    setRotateConfirm(null)
    setRotatedSecret(null)
    setSecretCopied(false)
  }

  const formatDate = (iso: string | null | undefined) => {
    if (!iso) return '—'
    return new Date(iso).toLocaleDateString('en-GB', {
      day: 'numeric', month: 'short', year: 'numeric',
    })
  }

  const formatDateTime = (iso: string | null | undefined) => {
    if (!iso) return '—'
    return new Date(iso).toLocaleString('en-GB', {
      day: 'numeric', month: 'short', year: 'numeric',
      hour: '2-digit', minute: '2-digit',
    })
  }

  const statusBadge = (endpoint: WebhookEndpointResponse) => {
    if (!endpoint.is_active) {
      return <span className="badge badge-revoked">Revoked</span>
    }
    return <span className="badge badge-active">Active</span>
  }

  const deliveryStatusBadge = (status: string, signatureValid: boolean) => {
    if (!signatureValid) {
      return <span className="badge badge-revoked">Invalid Sig</span>
    }
    switch (status) {
      case 'processed':
        return <span className="badge badge-active">Processed</span>
      case 'failed':
        return <span className="badge badge-revoked">Failed</span>
      case 'received':
        return <span className="badge badge-viewer">Received</span>
      default:
        return <span className="badge badge-viewer">{status}</span>
    }
  }

  const rotateEndpoint = endpoints.find(e => e.id === rotateConfirm)
  const deliveryEndpoint = endpoints.find(e => e.id === deliveryEndpointId)

  return (
    <div className="api-keys-section surface-bench">
      <div className="section-header" style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '24px' }}>
        <div>
          <h3 className="bench-header"><span className="container-title">Your Webhooks</span></h3>
          <p>
            Manage webhook endpoints for automated evidence ingestion. Use the Collection Wizard to create new endpoints.
          </p>
        </div>
        <button
          className="btn btn-primary"
          onClick={() => toast('To create a webhook endpoint, use the Collection Wizard when configuring an evidence item.', { icon: 'ℹ️', duration: 5000 })}
          style={{ whiteSpace: 'nowrap' }}
        >
          + Create (via Wizard)
        </button>
      </div>

      {loading ? (
        <div style={{ textAlign: 'center', padding: '2rem' }}>
          <div className="loading-spinner" />
        </div>
      ) : endpoints.length === 0 ? (
        <div className="api-keys-table-container" style={{ textAlign: 'center', padding: '2rem', color: 'var(--muted)' }}>
          No webhook endpoints configured. Create one using the Collection Wizard when setting up an evidence item.
        </div>
      ) : (
        <div className="api-keys-table-container">
          <table className="api-key-table">
            <thead>
              <tr>
                <th>Name</th>
                <th>Secret Prefix</th>
                <th>Evidence IDs</th>
                <th>Deliveries</th>
                <th>Last Delivery</th>
                <th>Created</th>
                <th>Status</th>
                <th>Actions</th>
              </tr>
            </thead>
            <tbody>
              {endpoints.map(ep => (
                <>
                  <tr
                    key={ep.id}
                    style={{ opacity: ep.is_active ? 1 : 0.55, cursor: 'pointer' }}
                    onClick={() => setExpandedId(expandedId === ep.id ? null : ep.id)}
                  >
                    <td style={{ fontWeight: 500 }}>{ep.name}</td>
                    <td><code className="api-key-prefix">{ep.secret_prefix}...</code></td>
                    <td>
                      {ep.allowed_evidence_ids && ep.allowed_evidence_ids.length > 0
                        ? ep.allowed_evidence_ids.length === 1
                          ? ep.allowed_evidence_ids[0]
                          : `${ep.allowed_evidence_ids.length} evidence IDs`
                        : <span style={{ color: 'var(--muted)' }}>Any</span>
                      }
                    </td>
                    <td>{ep.delivery_count}</td>
                    <td>{formatDate(ep.last_delivery_at)}</td>
                    <td>{formatDate(ep.created_at)}</td>
                    <td>{statusBadge(ep)}</td>
                    <td onClick={e => e.stopPropagation()} style={{ whiteSpace: 'nowrap' }}>
                      <button
                        className="btn btn-secondary btn-sm"
                        style={{ marginRight: '4px' }}
                        onClick={() => handleViewDeliveries(ep.id)}
                        title="View delivery logs"
                      >
                        Logs
                      </button>
                      {ep.is_active && (
                        <>
                          <button
                            className="btn btn-secondary btn-sm"
                            style={{ marginRight: '4px' }}
                            onClick={() => setRotateConfirm(ep.id)}
                            title="Rotate signing secret"
                          >
                            Rotate
                          </button>
                          <button
                            className="btn btn-danger btn-sm"
                            onClick={() => setRevokeConfirm(ep)}
                            title="Revoke endpoint"
                          >
                            Revoke
                          </button>
                        </>
                      )}
                    </td>
                  </tr>
                  {expandedId === ep.id && (
                    <tr key={`${ep.id}-detail`}>
                      <td colSpan={8} style={{ background: 'var(--surface-secondary, var(--bg-secondary))', padding: '12px 16px' }}>
                        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(260px, 1fr))', gap: '12px', fontSize: '0.875rem' }}>
                          <div>
                            <strong>Endpoint ID</strong>
                            <div style={{ fontFamily: 'monospace', marginTop: '4px', wordBreak: 'break-all' }}>{ep.id}</div>
                          </div>
                          <div>
                            <strong>Description</strong>
                            <div style={{ marginTop: '4px' }}>{ep.description || <span style={{ color: 'var(--muted)' }}>—</span>}</div>
                          </div>
                          <div>
                            <strong>Secret Prefix</strong>
                            <div style={{ marginTop: '4px' }}><code>{ep.secret_prefix}...</code></div>
                          </div>
                          <div>
                            <strong>Rate Limit</strong>
                            <div style={{ marginTop: '4px' }}>{ep.rate_limit_per_minute ? `${ep.rate_limit_per_minute}/min` : 'Default'}</div>
                          </div>
                          {ep.allowed_evidence_ids && ep.allowed_evidence_ids.length > 0 && (
                            <div style={{ gridColumn: '1 / -1' }}>
                              <strong>Allowed Evidence IDs</strong>
                              <div style={{ marginTop: '4px', display: 'flex', flexWrap: 'wrap', gap: '4px' }}>
                                {ep.allowed_evidence_ids.map(id => (
                                  <code key={id} style={{ background: 'var(--bg-tertiary)', padding: '2px 6px', borderRadius: '4px', fontSize: '0.8rem' }}>{id}</code>
                                ))}
                              </div>
                            </div>
                          )}
                          <div>
                            <strong>Created</strong>
                            <div style={{ marginTop: '4px' }}>{formatDateTime(ep.created_at)}</div>
                          </div>
                          <div>
                            <strong>Last Updated</strong>
                            <div style={{ marginTop: '4px' }}>{formatDateTime(ep.updated_at)}</div>
                          </div>
                        </div>
                      </td>
                    </tr>
                  )}
                </>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* Rotate Secret Modal */}
      {rotateConfirm !== null && (
        <div className="modal-overlay" onClick={closeRotateModal}>
          <div className="modal-content" onClick={e => e.stopPropagation()}>
            <div className="modal-header">
              <h2>{rotatedSecret ? 'New Signing Secret' : 'Rotate Signing Secret'}</h2>
              <button className="modal-close" onClick={closeRotateModal} aria-label="Close">
                <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                  <line x1="18" y1="6" x2="6" y2="18" />
                  <line x1="6" y1="6" x2="18" y2="18" />
                </svg>
              </button>
            </div>

            {!rotatedSecret ? (
              <>
                <div className="modal-body">
                  <p className="modal-description">
                    Rotating the secret for <strong>{rotateEndpoint?.name}</strong> will immediately invalidate the old secret.
                    All webhook deliveries using the old secret will start failing until you update your receiver.
                  </p>
                  <p style={{ color: 'var(--warning, #f59e0b)', fontSize: '0.875rem' }}>
                    The new secret will be shown only once — make sure to copy it before closing this dialog.
                  </p>
                </div>
                <div className="modal-footer">
                  <button className="btn-secondary" onClick={closeRotateModal}>
                    Cancel
                  </button>
                  <button
                    className="btn-primary"
                    onClick={handleRotateConfirm}
                    disabled={rotating}
                  >
                    {rotating ? 'Rotating...' : 'Rotate Secret'}
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
                    Copy this secret now. It will not be shown again.
                  </div>

                  <div className="api-key-secret-box">
                    {rotatedSecret.plaintext_secret}
                  </div>

                  <button
                    className={`api-key-copy-btn ${secretCopied ? 'copied' : ''}`}
                    onClick={() => handleCopySecret(rotatedSecret.plaintext_secret)}
                  >
                    {secretCopied ? (
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
                  <button className="btn-primary" onClick={closeRotateModal}>
                    Done
                  </button>
                </div>
              </>
            )}
          </div>
        </div>
      )}

      {/* Revoke Confirm Modal */}
      {revokeConfirm && (
        <div className="modal-overlay" onClick={() => setRevokeConfirm(null)}>
          <div className="modal-content" onClick={e => e.stopPropagation()}>
            <div className="modal-header">
              <h2>Revoke Webhook Endpoint</h2>
              <button className="modal-close" onClick={() => setRevokeConfirm(null)} aria-label="Close">
                <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                  <line x1="18" y1="6" x2="6" y2="18" />
                  <line x1="6" y1="6" x2="18" y2="18" />
                </svg>
              </button>
            </div>
            <div className="modal-body">
              <p className="modal-description">
                Are you sure you want to revoke <strong>{revokeConfirm.name}</strong>?
                Future deliveries to this endpoint will be rejected. This action cannot be undone.
              </p>
            </div>
            <div className="modal-footer">
              <button className="btn-secondary" onClick={() => setRevokeConfirm(null)}>
                Cancel
              </button>
              <button
                className="btn btn-danger"
                onClick={handleRevokeConfirm}
                disabled={revoking}
              >
                {revoking ? 'Revoking...' : 'Revoke Endpoint'}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Delivery Logs Modal */}
      {deliveryEndpointId && (
        <div className="modal-overlay" onClick={() => setDeliveryEndpointId(null)}>
          <div className="modal-content" style={{ maxWidth: '780px', width: '95vw' }} onClick={e => e.stopPropagation()}>
            <div className="modal-header">
              <h2>Delivery Logs — {deliveryEndpoint?.name}</h2>
              <button className="modal-close" onClick={() => setDeliveryEndpointId(null)} aria-label="Close">
                <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                  <line x1="18" y1="6" x2="6" y2="18" />
                  <line x1="6" y1="6" x2="18" y2="18" />
                </svg>
              </button>
            </div>
            <div className="modal-body">
              {deliveriesLoading ? (
                <div style={{ textAlign: 'center', padding: '2rem' }}>
                  <div className="loading-spinner" />
                </div>
              ) : deliveries.length === 0 ? (
                <div style={{ textAlign: 'center', padding: '2rem', color: 'var(--muted)' }}>
                  No delivery logs found for this endpoint.
                </div>
              ) : (
                <div className="api-keys-table-container">
                  <table className="api-key-table" style={{ fontSize: '0.85rem' }}>
                    <thead>
                      <tr>
                        <th>Received</th>
                        <th>Evidence ID</th>
                        <th>Status</th>
                        <th>Error</th>
                        <th>IP</th>
                      </tr>
                    </thead>
                    <tbody>
                      {deliveries.map(d => (
                        <tr key={d.id}>
                          <td style={{ whiteSpace: 'nowrap' }}>{formatDateTime(d.created_at)}</td>
                          <td><code style={{ fontSize: '0.8rem' }}>{d.evidence_id}</code></td>
                          <td>{deliveryStatusBadge(d.status, d.signature_valid)}</td>
                          <td style={{ maxWidth: '240px', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                            {d.error_message || <span style={{ color: 'var(--muted)' }}>—</span>}
                          </td>
                          <td style={{ color: 'var(--muted)', fontSize: '0.8rem' }}>{d.ip_address || '—'}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
            <div className="modal-footer">
              <button className="btn-primary" onClick={() => setDeliveryEndpointId(null)}>
                Close
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
