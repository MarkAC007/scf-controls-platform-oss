import { useState, useEffect, useCallback } from 'react'
import { getOrgAuditLog } from '../data/apiClient'
import type { AuditLogEntry, AuditLogListResponse } from '../types'

interface AuditLogPageProps {
  organizationId: string
}

// ── Helpers (adapted from AuditLogPanel.tsx for consistency) ──

const FIELD_LABELS: Record<string, string> = {
  selected: 'Scoped',
  implementation_status: 'Status',
  priority: 'Priority',
  owner: 'Owner',
  assigned_to: 'Assigned To',
  maturity_level: 'Maturity Level',
  selection_reason: 'Selection Reason',
  target_date: 'Target Date',
  completion_date: 'Completion Date',
  implementation_notes: 'Notes',
  control_weighting: 'Control Weighting',
  validation_cadence: 'Validation Cadence',
  nist_csf_function: 'NIST CSF Function',
  control_question: 'Control Question',
  related_documentation: 'Documentation',
  custom_fields: 'Custom Fields',
}

function formatValue(field: string, value: string | undefined): string {
  if (!value || value === 'None') return '\u2014'
  if (field === 'selected') return value === 'True' ? 'Yes' : 'No'
  if (field === 'implementation_status') return value.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase())
  if (field === 'priority') return value.charAt(0).toUpperCase() + value.slice(1)
  if (field === 'maturity_level') return value.toUpperCase()
  if (field.includes('date') && value !== '\u2014') {
    try {
      return new Date(value + 'T00:00:00').toLocaleDateString('en-US', { year: 'numeric', month: 'short', day: 'numeric' })
    } catch { return value }
  }
  return value.length > 80 ? value.substring(0, 77) + '...' : value
}

function formatTime(iso: string): string {
  const d = new Date(iso)
  const now = new Date()
  const diffMs = now.getTime() - d.getTime()
  const diffMin = Math.floor(diffMs / 60000)
  if (diffMin < 1) return 'just now'
  if (diffMin < 60) return `${diffMin}m ago`
  const diffHr = Math.floor(diffMin / 60)
  if (diffHr < 24) return `${diffHr}h ago`
  const diffDays = Math.floor(diffHr / 24)
  if (diffDays < 7) return `${diffDays}d ago`
  return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' })
}

const SOURCE_BADGE: Record<string, { label: string; color: string; bg: string }> = {
  ui: { label: 'UI', color: 'var(--success)', bg: 'var(--success-bg)' },
  api_key: { label: 'API', color: 'var(--info)', bg: 'var(--info-bg)' },
  mcp: { label: 'MCP', color: 'var(--purple)', bg: 'var(--purple-bg)' },
  system: { label: 'SYS', color: 'var(--muted)', bg: 'var(--secondary)' },
}

function SourceBadge({ source }: { source?: string }) {
  if (!source) return null
  const badge = SOURCE_BADGE[source]
  if (!badge) return <span style={{ fontSize: 11, color: 'var(--muted)' }}>{source}</span>
  return (
    <span style={{
      fontSize: 11, fontWeight: 600,
      padding: '1px 6px', borderRadius: 4,
      color: badge.color, backgroundColor: badge.bg,
    }}>
      {badge.label}
    </span>
  )
}

// ── Filter state ──

interface Filters {
  entity_type: string
  action: string
  action_source: string
  date_from: string
  date_to: string
  search_text: string
  actor_id: string
}

const EMPTY_FILTERS: Filters = {
  entity_type: '',
  action: '',
  action_source: '',
  date_from: '',
  date_to: '',
  search_text: '',
  actor_id: '',
}

const PAGE_SIZE = 50

// ── Styles ──

const tableStyle: React.CSSProperties = {
  width: '100%', borderCollapse: 'collapse', fontSize: 13,
}

const thStyle: React.CSSProperties = {
  textAlign: 'left', padding: '8px 10px', borderBottom: '2px solid var(--border)',
  color: 'var(--muted)', fontWeight: 600, fontSize: 12, whiteSpace: 'nowrap',
}

const tdStyle: React.CSSProperties = {
  padding: '7px 10px', borderBottom: '1px solid var(--border)',
  color: 'var(--text)', verticalAlign: 'top',
}

const inputStyle: React.CSSProperties = {
  padding: '5px 8px', borderRadius: 6, border: '1px solid var(--border)',
  fontSize: 12, color: 'var(--text)', background: 'var(--surface)', minWidth: 0,
}

const selectStyle: React.CSSProperties = {
  ...inputStyle, cursor: 'pointer',
}

const btnStyle: React.CSSProperties = {
  padding: '5px 12px', borderRadius: 6, border: '1px solid var(--border)',
  fontSize: 12, cursor: 'pointer', background: 'var(--surface)', color: 'var(--muted)',
}

const btnPrimaryStyle: React.CSSProperties = {
  ...btnStyle, background: 'var(--info)', color: 'white', border: '1px solid var(--info)',
}

// ── Component ──

export default function AuditLogPage({ organizationId }: AuditLogPageProps) {
  const [entries, setEntries] = useState<AuditLogEntry[]>([])
  const [total, setTotal] = useState(0)
  const [offset, setOffset] = useState(0)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [filters, setFilters] = useState<Filters>(EMPTY_FILTERS)
  const [appliedFilters, setAppliedFilters] = useState<Filters>(EMPTY_FILTERS)

  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE))
  const currentPage = Math.floor(offset / PAGE_SIZE) + 1

  const fetchData = useCallback(async (f: Filters, pageOffset: number) => {
    setLoading(true)
    setError(null)
    try {
      const result: AuditLogListResponse = await getOrgAuditLog(organizationId, {
        entity_type: f.entity_type || undefined,
        action: f.action || undefined,
        action_source: f.action_source || undefined,
        actor_id: f.actor_id || undefined,
        date_from: f.date_from || undefined,
        date_to: f.date_to || undefined,
        search_text: f.search_text || undefined,
        limit: PAGE_SIZE,
        offset: pageOffset,
      })
      setEntries(result.entries)
      setTotal(result.total)
      setOffset(pageOffset)
    } catch (err: any) {
      setError(err.message || 'Failed to load audit log')
      setEntries([])
      setTotal(0)
    } finally {
      setLoading(false)
    }
  }, [organizationId])

  // Initial load and when org changes
  useEffect(() => {
    setFilters(EMPTY_FILTERS)
    setAppliedFilters(EMPTY_FILTERS)
    setOffset(0)
    fetchData(EMPTY_FILTERS, 0)
  }, [organizationId, fetchData])

  const handleApplyFilters = () => {
    setAppliedFilters({ ...filters })
    setOffset(0)
    fetchData(filters, 0)
  }

  const handleClearFilters = () => {
    setFilters(EMPTY_FILTERS)
    setAppliedFilters(EMPTY_FILTERS)
    setOffset(0)
    fetchData(EMPTY_FILTERS, 0)
  }

  const handlePrev = () => {
    const newOffset = Math.max(0, offset - PAGE_SIZE)
    fetchData(appliedFilters, newOffset)
  }

  const handleNext = () => {
    const newOffset = offset + PAGE_SIZE
    if (newOffset < total) {
      fetchData(appliedFilters, newOffset)
    }
  }

  const updateFilter = (key: keyof Filters, value: string) => {
    setFilters(prev => ({ ...prev, [key]: value }))
  }

  return (
    <div style={{ padding: '24px 32px', maxWidth: 1400 }}>
      <div style={{ marginBottom: 20 }}>
        <h2 style={{ margin: 0, fontSize: 20, fontWeight: 600, color: 'var(--text)' }}>
          Audit Log
        </h2>
        <p style={{ margin: '4px 0 0', fontSize: 13, color: 'var(--muted)' }}>
          Complete change history across all entities in this organization.
        </p>
      </div>

      {/* Filter Bar */}
      <div style={{
        display: 'flex', flexWrap: 'wrap', gap: 8, alignItems: 'flex-end',
        padding: '12px 16px', background: 'var(--secondary)', borderRadius: 8,
        border: '1px solid var(--border)', marginBottom: 16,
      }}>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
          <label style={{ fontSize: 11, color: 'var(--muted)', fontWeight: 500 }}>Entity Type</label>
          <select
            value={filters.entity_type}
            onChange={e => updateFilter('entity_type', e.target.value)}
            style={selectStyle}
          >
            <option value="">All</option>
            <option value="scoped_control">Scoped Control</option>
            <option value="evidence">Evidence</option>
            <option value="risk">Risk</option>
            <option value="vendor">Vendor</option>
            <option value="system">System</option>
            <option value="user">User</option>
            <option value="organization">Organization</option>
            <option value="webhook">Webhook</option>
          </select>
        </div>

        <div style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
          <label style={{ fontSize: 11, color: 'var(--muted)', fontWeight: 500 }}>Action</label>
          <select
            value={filters.action}
            onChange={e => updateFilter('action', e.target.value)}
            style={selectStyle}
          >
            <option value="">All</option>
            <option value="create">Create</option>
            <option value="update">Update</option>
            <option value="delete">Delete</option>
          </select>
        </div>

        <div style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
          <label style={{ fontSize: 11, color: 'var(--muted)', fontWeight: 500 }}>Source</label>
          <select
            value={filters.action_source}
            onChange={e => updateFilter('action_source', e.target.value)}
            style={selectStyle}
          >
            <option value="">All</option>
            <option value="ui">UI</option>
            <option value="api_key">API</option>
            <option value="mcp">MCP</option>
            <option value="system">System</option>
          </select>
        </div>

        <div style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
          <label style={{ fontSize: 11, color: 'var(--muted)', fontWeight: 500 }}>From</label>
          <input
            type="date"
            value={filters.date_from}
            onChange={e => updateFilter('date_from', e.target.value)}
            style={inputStyle}
          />
        </div>

        <div style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
          <label style={{ fontSize: 11, color: 'var(--muted)', fontWeight: 500 }}>To</label>
          <input
            type="date"
            value={filters.date_to}
            onChange={e => updateFilter('date_to', e.target.value)}
            style={inputStyle}
          />
        </div>

        <div style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
          <label style={{ fontSize: 11, color: 'var(--muted)', fontWeight: 500 }}>Actor ID</label>
          <input
            type="text"
            placeholder="User ID..."
            value={filters.actor_id}
            onChange={e => updateFilter('actor_id', e.target.value)}
            style={{ ...inputStyle, width: 120 }}
          />
        </div>

        <div style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
          <label style={{ fontSize: 11, color: 'var(--muted)', fontWeight: 500 }}>Search</label>
          <input
            type="text"
            placeholder="Search text..."
            value={filters.search_text}
            onChange={e => updateFilter('search_text', e.target.value)}
            onKeyDown={e => e.key === 'Enter' && handleApplyFilters()}
            style={{ ...inputStyle, width: 160 }}
          />
        </div>

        <div style={{ display: 'flex', gap: 6 }}>
          <button onClick={handleApplyFilters} style={btnPrimaryStyle}>Apply</button>
          <button onClick={handleClearFilters} style={btnStyle}>Clear</button>
        </div>
      </div>

      {/* Loading state */}
      {loading && (
        <div style={{ padding: 40, textAlign: 'center', color: 'var(--muted)', fontSize: 14 }}>
          Loading audit log...
        </div>
      )}

      {/* Error state */}
      {error && !loading && (
        <div style={{
          padding: 20, textAlign: 'center', color: 'var(--destructive)', fontSize: 14,
          background: 'var(--error-bg)', borderRadius: 8, border: '1px solid var(--destructive)',
        }}>
          Error: {error}
          <button onClick={() => fetchData(appliedFilters, offset)} style={{ ...btnStyle, marginLeft: 12 }}>
            Retry
          </button>
        </div>
      )}

      {/* Empty state */}
      {!loading && !error && entries.length === 0 && (
        <div style={{ padding: 40, textAlign: 'center', color: 'var(--muted)', fontSize: 14 }}>
          No audit log entries found matching your filters.
        </div>
      )}

      {/* Data table */}
      {!loading && !error && entries.length > 0 && (
        <>
          <div style={{ overflowX: 'auto' }}>
            <table style={tableStyle}>
              <thead>
                <tr>
                  <th style={thStyle}>Timestamp</th>
                  <th style={thStyle}>Actor</th>
                  <th style={thStyle}>Action</th>
                  <th style={thStyle}>Entity Type</th>
                  <th style={thStyle}>Entity ID</th>
                  <th style={thStyle}>Field</th>
                  <th style={thStyle}>Change</th>
                  <th style={thStyle}>Source</th>
                </tr>
              </thead>
              <tbody>
                {entries.map(entry => (
                  <tr key={entry.id} style={{ transition: 'background 0.15s' }}
                    onMouseEnter={e => (e.currentTarget.style.background = 'var(--surface-hover)')}
                    onMouseLeave={e => (e.currentTarget.style.background = 'transparent')}
                  >
                    <td style={{ ...tdStyle, whiteSpace: 'nowrap', fontSize: 12 }}>
                      <span title={new Date(entry.changed_at).toLocaleString()}>
                        {formatTime(entry.changed_at)}
                      </span>
                    </td>
                    <td style={{ ...tdStyle, fontSize: 12 }}>
                      {entry.changed_by_email || entry.changed_by_user_id || '\u2014'}
                    </td>
                    <td style={tdStyle}>
                      <span style={{
                        display: 'inline-block', padding: '1px 7px', borderRadius: 4,
                        fontSize: 11, fontWeight: 600,
                        color: entry.action === 'create' ? 'var(--success)'
                             : entry.action === 'delete' ? 'var(--destructive)'
                             : 'var(--info)',
                        background: entry.action === 'create' ? 'var(--success-bg)'
                                  : entry.action === 'delete' ? 'var(--error-bg)'
                                  : 'var(--info-bg)',
                      }}>
                        {entry.action}
                      </span>
                    </td>
                    <td style={{ ...tdStyle, fontSize: 12 }}>
                      {entry.entity_type.replace(/_/g, ' ')}
                    </td>
                    <td style={{ ...tdStyle, fontSize: 11, fontFamily: 'monospace', color: 'var(--muted)' }}>
                      {entry.scf_id || (entry.entity_id.length > 12
                        ? entry.entity_id.substring(0, 12) + '...'
                        : entry.entity_id)}
                    </td>
                    <td style={{ ...tdStyle, fontSize: 12 }}>
                      {entry.field_name
                        ? (FIELD_LABELS[entry.field_name] || entry.field_name)
                        : '\u2014'}
                    </td>
                    <td style={{ ...tdStyle, fontSize: 12, maxWidth: 280 }}>
                      {entry.action === 'create' ? (
                        <span style={{ color: 'var(--success)', fontStyle: 'italic' }}>created</span>
                      ) : entry.action === 'delete' ? (
                        <span style={{ color: 'var(--destructive)', fontStyle: 'italic' }}>deleted</span>
                      ) : (
                        <span style={{ display: 'flex', gap: 4, flexWrap: 'wrap', alignItems: 'baseline' }}>
                          <span style={{ textDecoration: 'line-through', opacity: 0.5, fontSize: 11 }}>
                            {formatValue(entry.field_name || '', entry.old_value)}
                          </span>
                          <span style={{ color: 'var(--muted)' }}>{'\u2192'}</span>
                          <span style={{ color: 'var(--success)', fontWeight: 500, fontSize: 11 }}>
                            {formatValue(entry.field_name || '', entry.new_value)}
                          </span>
                        </span>
                      )}
                    </td>
                    <td style={tdStyle}>
                      <SourceBadge source={entry.action_source} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {/* Pagination */}
          <div style={{
            display: 'flex', justifyContent: 'space-between', alignItems: 'center',
            padding: '12px 0', borderTop: '1px solid var(--border)', marginTop: 8,
          }}>
            <span style={{ fontSize: 12, color: 'var(--muted)' }}>
              Showing {offset + 1}&ndash;{Math.min(offset + PAGE_SIZE, total)} of {total} entries
            </span>
            <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
              <button
                onClick={handlePrev}
                disabled={offset === 0}
                style={{
                  ...btnStyle,
                  opacity: offset === 0 ? 0.4 : 1,
                  cursor: offset === 0 ? 'not-allowed' : 'pointer',
                }}
              >
                Previous
              </button>
              <span style={{ fontSize: 12, color: 'var(--muted)' }}>
                Page {currentPage} of {totalPages}
              </span>
              <button
                onClick={handleNext}
                disabled={offset + PAGE_SIZE >= total}
                style={{
                  ...btnStyle,
                  opacity: offset + PAGE_SIZE >= total ? 0.4 : 1,
                  cursor: offset + PAGE_SIZE >= total ? 'not-allowed' : 'pointer',
                }}
              >
                Next
              </button>
            </div>
          </div>
        </>
      )}
    </div>
  )
}
