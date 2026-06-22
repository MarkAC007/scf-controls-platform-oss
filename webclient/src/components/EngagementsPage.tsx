import { useState, useEffect } from 'react'
import {
  listEngagements,
  createEngagement,
  deleteEngagement,
  type AuditEngagement,
  type AuditEngagementCreate,
} from '../data/apiClient'
import { fetchFrameworks } from '../data/catalogApi'

interface EngagementsPageProps {
  organizationId: string
}

const STATUS_LABELS: Record<string, string> = {
  draft: 'Draft',
  active: 'Active',
  under_review: 'Under Review',
  closed: 'Closed',
}

const STATUS_COLORS: Record<string, { bg: string; color: string }> = {
  draft:        { bg: 'var(--secondary)', color: 'var(--muted)' },
  active:       { bg: 'var(--success-bg)', color: 'var(--success)' },
  under_review: { bg: 'var(--warning-bg)', color: 'var(--warning)' },
  closed:       { bg: 'var(--error-bg)', color: 'var(--text)' },
}

// ---------------------------------------------------------------------------
// Create Engagement Drawer
// ---------------------------------------------------------------------------

interface CreateDrawerProps {
  organizationId: string
  onClose: () => void
  onCreated: () => void
}

function CreateEngagementDrawer({ organizationId, onClose, onCreated }: CreateDrawerProps) {
  const [name, setName] = useState('')
  const [selectedFrameworks, setSelectedFrameworks] = useState<string[]>([])
  const [startDate, setStartDate] = useState('')
  const [endDate, setEndDate] = useState('')
  const [availableFrameworks, setAvailableFrameworks] = useState<string[]>([])
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    fetchFrameworks(false)
      .then(fws => setAvailableFrameworks(fws.map(f => f.id)))
      .catch(() => {})
  }, [])

  const toggleFramework = (fw: string) => {
    setSelectedFrameworks(prev =>
      prev.includes(fw) ? prev.filter(f => f !== fw) : [...prev, fw]
    )
  }

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!name.trim()) { setError('Name is required'); return }
    if (selectedFrameworks.length === 0) { setError('Select at least one framework'); return }

    setSubmitting(true)
    setError(null)
    try {
      const payload: AuditEngagementCreate = {
        name: name.trim(),
        frameworks: selectedFrameworks,
        start_date: startDate || null,
        end_date: endDate || null,
      }
      await createEngagement(organizationId, payload)
      onCreated()
    } catch (err: any) {
      setError(err?.message ?? 'Failed to create engagement')
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div style={{
      position: 'fixed', inset: 0, zIndex: 50,
      display: 'flex', justifyContent: 'flex-end',
      background: 'rgba(0,0,0,0.35)',
    }} onClick={onClose}>
      <div
        style={{
          width: 480, maxWidth: '100vw', height: '100%',
          background: 'var(--card)', boxShadow: '-4px 0 24px rgba(0,0,0,0.25)',
          display: 'flex', flexDirection: 'column',
          overflowY: 'auto',
        }}
        onClick={e => e.stopPropagation()}
      >
        {/* Header */}
        <div style={{
          padding: '20px 24px', borderBottom: '1px solid var(--border)',
          display: 'flex', justifyContent: 'space-between', alignItems: 'center',
        }}>
          <h2 style={{ margin: 0, fontSize: 18, fontWeight: 600, color: 'var(--text)' }}>
            New Engagement
          </h2>
          <button
            onClick={onClose}
            style={{ background: 'none', border: 'none', cursor: 'pointer', fontSize: 20, color: 'var(--muted)' }}
          >&times;</button>
        </div>

        {/* Form */}
        <form onSubmit={handleSubmit} style={{ padding: 24, flex: 1, display: 'flex', flexDirection: 'column', gap: 20 }}>
          {error && (
            <div style={{
              padding: '10px 14px', background: 'var(--error-bg)', border: '1px solid var(--border)',
              borderRadius: 6, color: 'var(--text)', fontSize: 13,
            }}>{error}</div>
          )}

          <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
            <label style={{ fontSize: 13, fontWeight: 600, color: 'var(--text)' }}>
              Engagement Name <span style={{ color: '#ef4444' }}>*</span>
            </label>
            <input
              type="text"
              value={name}
              onChange={e => setName(e.target.value)}
              placeholder="e.g. ISO 27001:2022 Certification Audit 2026"
              style={{
                padding: '8px 12px', border: '1px solid var(--border)', borderRadius: 6,
                fontSize: 14, outline: 'none', width: '100%', boxSizing: 'border-box',
                background: 'var(--panel)', color: 'var(--text)',
              }}
              autoFocus
            />
          </div>

          <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            <label style={{ fontSize: 13, fontWeight: 600, color: 'var(--text)' }}>
              Frameworks <span style={{ color: '#ef4444' }}>*</span>
            </label>
            <p style={{ margin: 0, fontSize: 12, color: 'var(--muted)' }}>
              Controls for selected frameworks will be auto-materialised as scope.
            </p>
            <div style={{
              maxHeight: 220, overflowY: 'auto', border: '1px solid var(--border)',
              borderRadius: 6, padding: 8, display: 'flex', flexDirection: 'column', gap: 2,
              background: 'var(--card)',
            }}>
              {availableFrameworks.length === 0 ? (
                <div style={{ padding: 8, color: 'var(--muted)', fontSize: 13 }}>Loading frameworks…</div>
              ) : (
                availableFrameworks.map(fw => (
                  <label
                    key={fw}
                    style={{
                      display: 'flex', alignItems: 'center', gap: 8,
                      padding: '6px 8px', borderRadius: 4, cursor: 'pointer',
                      background: selectedFrameworks.includes(fw) ? 'var(--accent-muted)' : 'transparent',
                      fontSize: 13, color: 'var(--text)',
                    }}
                  >
                    <input
                      type="checkbox"
                      checked={selectedFrameworks.includes(fw)}
                      onChange={() => toggleFramework(fw)}
                      style={{ cursor: 'pointer' }}
                    />
                    {fw}
                  </label>
                ))
              )}
            </div>
            {selectedFrameworks.length > 0 && (
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4 }}>
                {selectedFrameworks.map(fw => (
                  <span key={fw} style={{
                    padding: '2px 8px', borderRadius: 10,
                    background: 'var(--accent-muted)', color: 'var(--primary)', fontSize: 11, fontWeight: 500,
                  }}>{fw}</span>
                ))}
              </div>
            )}
          </div>

          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
              <label style={{ fontSize: 13, fontWeight: 600, color: 'var(--text)' }}>Start Date</label>
              <input
                type="date"
                value={startDate}
                onChange={e => setStartDate(e.target.value)}
                style={{
                  padding: '8px 12px', border: '1px solid var(--border)', borderRadius: 6,
                  fontSize: 14, outline: 'none', width: '100%', boxSizing: 'border-box',
                  background: 'var(--panel)', color: 'var(--text)',
                }}
              />
            </div>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
              <label style={{ fontSize: 13, fontWeight: 600, color: 'var(--text)' }}>End Date</label>
              <input
                type="date"
                value={endDate}
                onChange={e => setEndDate(e.target.value)}
                style={{
                  padding: '8px 12px', border: '1px solid var(--border)', borderRadius: 6,
                  fontSize: 14, outline: 'none', width: '100%', boxSizing: 'border-box',
                  background: 'var(--panel)', color: 'var(--text)',
                }}
              />
            </div>
          </div>

          <div style={{ marginTop: 'auto', display: 'flex', gap: 10, paddingTop: 16 }}>
            <button
              type="submit"
              disabled={submitting}
              style={{
                flex: 1, padding: '10px 0', borderRadius: 6, border: 'none',
                background: submitting ? 'var(--muted-bg)' : 'var(--primary)', color: 'var(--primary-foreground)',
                fontSize: 14, fontWeight: 600, cursor: submitting ? 'not-allowed' : 'pointer',
              }}
            >
              {submitting ? 'Creating…' : 'Create Engagement'}
            </button>
            <button
              type="button"
              onClick={onClose}
              style={{
                padding: '10px 20px', borderRadius: 6, border: '1px solid var(--border)',
                background: 'var(--secondary)', color: 'var(--text)', fontSize: 14, cursor: 'pointer',
              }}
            >
              Cancel
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Main Page
// ---------------------------------------------------------------------------

export default function EngagementsPage({ organizationId }: EngagementsPageProps) {
  const [engagements, setEngagements] = useState<AuditEngagement[]>([])
  const [loading, setLoading] = useState(true)
  const [showCreateDrawer, setShowCreateDrawer] = useState(false)
  const [deleting, setDeleting] = useState<string | null>(null)

  const load = async () => {
    setLoading(true)
    try {
      const data = await listEngagements(organizationId)
      setEngagements(data)
    } catch {
      setEngagements([])
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { load() }, [organizationId])

  const handleDelete = async (engagement: AuditEngagement) => {
    if (!window.confirm(`Delete engagement "${engagement.name}"? This cannot be undone.`)) return
    setDeleting(engagement.id)
    try {
      await deleteEngagement(organizationId, engagement.id)
      await load()
    } catch (err: any) {
      alert(err?.message ?? 'Failed to delete engagement')
    } finally {
      setDeleting(null)
    }
  }

  const handleCreated = () => {
    setShowCreateDrawer(false)
    load()
  }

  return (
    <div style={{ padding: '24px 32px', maxWidth: 1100, margin: '0 auto' }}>
      {/* Early development banner */}
      <div style={{
        display: 'flex', alignItems: 'center', gap: 10,
        padding: '10px 16px', marginBottom: 24, borderRadius: 8,
        background: 'var(--warning-bg)', border: '1px solid var(--warning)',
        color: 'var(--text)', fontSize: 14,
      }}>
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="var(--warning)" strokeWidth="2" style={{ flexShrink: 0 }}>
          <path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/>
          <line x1="12" y1="9" x2="12" y2="13"/>
          <line x1="12" y1="17" x2="12.01" y2="17"/>
        </svg>
        <span>This is a future feature currently in early development and is not yet functional.</span>
      </div>
      {/* Page header */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 24 }}>
        <div>
          <h1 style={{ margin: 0, fontSize: 24, fontWeight: 700, color: 'var(--text)' }}>
            Audit Engagements
          </h1>
          <p style={{ margin: '4px 0 0', color: 'var(--muted)', fontSize: 14 }}>
            Scoped workspaces for audit programmes. Each engagement auto-materialises its control scope from selected frameworks.
          </p>
        </div>
        <button
          onClick={() => setShowCreateDrawer(true)}
          style={{
            padding: '9px 18px', borderRadius: 6, border: 'none',
            background: 'var(--primary)', color: 'var(--primary-foreground)',
            fontSize: 14, fontWeight: 600, cursor: 'pointer',
            whiteSpace: 'nowrap',
          }}
        >
          + New Engagement
        </button>
      </div>

      {/* Content */}
      {loading ? (
        <div style={{ textAlign: 'center', padding: '64px 0', color: 'var(--muted)', fontSize: 15 }}>
          Loading engagements…
        </div>
      ) : engagements.length === 0 ? (
        <div style={{
          textAlign: 'center', padding: '80px 0',
          border: '2px dashed var(--border)', borderRadius: 12,
        }}>
          <div style={{ fontSize: 40, marginBottom: 12 }}>📋</div>
          <h3 style={{ margin: 0, color: 'var(--muted)', fontWeight: 600 }}>No engagements yet</h3>
          <p style={{ color: 'var(--muted)', fontSize: 14, marginTop: 6 }}>
            Create your first engagement to start scoping an audit programme.
          </p>
          <button
            onClick={() => setShowCreateDrawer(true)}
            style={{
              marginTop: 16, padding: '9px 18px', borderRadius: 6, border: 'none',
              background: 'var(--primary)', color: 'var(--primary-foreground)', fontSize: 14, fontWeight: 600,
              cursor: 'pointer',
            }}
          >
            + New Engagement
          </button>
        </div>
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
          {engagements.map(eng => {
            const statusStyle = STATUS_COLORS[eng.status] ?? { bg: 'var(--secondary)', color: 'var(--muted)' }
            return (
              <div
                key={eng.id}
                style={{
                  border: '1px solid var(--border)', borderRadius: 10,
                  padding: '18px 22px', background: 'var(--card)',
                  display: 'flex', alignItems: 'flex-start', gap: 16,
                  boxShadow: '0 1px 3px rgba(0,0,0,0.05)',
                }}
              >
                {/* Main content */}
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap', marginBottom: 6 }}>
                    <h3 style={{ margin: 0, fontSize: 16, fontWeight: 600, color: 'var(--text)' }}>
                      {eng.name}
                    </h3>
                    <span style={{
                      padding: '2px 10px', borderRadius: 10, fontSize: 12, fontWeight: 600,
                      background: statusStyle.bg, color: statusStyle.color,
                    }}>
                      {STATUS_LABELS[eng.status] ?? eng.status}
                    </span>
                  </div>

                  {/* Frameworks */}
                  <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, marginBottom: 10 }}>
                    {eng.frameworks.map(fw => (
                      <span key={fw} style={{
                        padding: '2px 8px', borderRadius: 10,
                        background: 'var(--accent-muted)', color: 'var(--primary)', fontSize: 12, fontWeight: 500,
                      }}>{fw}</span>
                    ))}
                  </div>

                  {/* Meta row */}
                  <div style={{ display: 'flex', gap: 20, fontSize: 13, color: 'var(--muted)', flexWrap: 'wrap' }}>
                    <span>
                      <strong style={{ color: 'var(--text)' }}>{eng.scope_count ?? 0}</strong> controls in scope
                    </span>
                    {eng.start_date && (
                      <span>Start: {new Date(eng.start_date).toLocaleDateString()}</span>
                    )}
                    {eng.end_date && (
                      <span>End: {new Date(eng.end_date).toLocaleDateString()}</span>
                    )}
                    <span>Created: {new Date(eng.created_at).toLocaleDateString()}</span>
                  </div>
                </div>

                {/* Actions */}
                <div style={{ display: 'flex', flexDirection: 'column', gap: 6, flexShrink: 0 }}>
                  {eng.status === 'draft' && (
                    <button
                      onClick={() => handleDelete(eng)}
                      disabled={deleting === eng.id}
                      style={{
                        padding: '6px 14px', borderRadius: 6, border: '1px solid var(--border)',
                        background: 'var(--card)', color: '#ef4444', fontSize: 12,
                        cursor: deleting === eng.id ? 'not-allowed' : 'pointer',
                        opacity: deleting === eng.id ? 0.6 : 1,
                      }}
                    >
                      {deleting === eng.id ? 'Deleting…' : 'Delete'}
                    </button>
                  )}
                </div>
              </div>
            )
          })}
        </div>
      )}

      {/* Create drawer */}
      {showCreateDrawer && (
        <CreateEngagementDrawer
          organizationId={organizationId}
          onClose={() => setShowCreateDrawer(false)}
          onCreated={handleCreated}
        />
      )}
    </div>
  )
}
