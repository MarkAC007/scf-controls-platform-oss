import { useState, useEffect } from 'react'
import { getAuditLog } from '../data/apiClient'
import type { AuditLogEntry } from '../types'

interface AuditLogPanelProps {
  scfId: string
  organizationId: string
}

/** Human-readable labels for tracked fields */
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

/** Format a raw field value for display */
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
  return value.length > 60 ? value.substring(0, 57) + '...' : value
}

/** Format timestamp to relative or absolute */
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

/** Icon for action type */
function actionIcon(action: string): string {
  switch (action) {
    case 'create': return '\u2795'
    case 'update': return '\u270F\uFE0F'
    case 'delete': return '\uD83D\uDDD1\uFE0F'
    default: return '\uD83D\uDCDD'
  }
}

/** Source badge styling */
const SOURCE_BADGE: Record<string, { label: string; color: string; bg: string }> = {
  ui: { label: 'UI', color: '#059669', bg: '#ecfdf5' },
  api_key: { label: 'API', color: '#2563eb', bg: '#eff6ff' },
  mcp: { label: 'MCP', color: '#7c3aed', bg: '#f5f3ff' },
  system: { label: 'SYS', color: '#6b7280', bg: '#f3f4f6' },
}

function SourceBadge({ source }: { source?: string }) {
  if (!source) return null
  const badge = SOURCE_BADGE[source]
  if (!badge) return null
  return (
    <span style={{
      fontSize: '0.7em',
      fontWeight: 600,
      padding: '1px 5px',
      borderRadius: '3px',
      color: badge.color,
      backgroundColor: badge.bg,
      marginLeft: '0.4rem',
    }}>
      {badge.label}
    </span>
  )
}

export function AuditLogPanel({ scfId, organizationId }: AuditLogPanelProps) {
  const [entries, setEntries] = useState<AuditLogEntry[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [total, setTotal] = useState(0)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setError(null)

    getAuditLog(scfId, organizationId, 100)
      .then(data => {
        if (!cancelled) {
          setEntries(data.entries)
          setTotal(data.total)
        }
      })
      .catch(err => {
        if (!cancelled) setError(err.message || 'Failed to load audit log')
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })

    return () => { cancelled = true }
  }, [scfId, organizationId])

  if (loading) {
    return <div style={{ padding: '1rem', color: 'var(--muted)', fontSize: '0.85em' }}>Loading change history...</div>
  }

  if (error) {
    return <div style={{ padding: '1rem', color: '#ef4444', fontSize: '0.85em' }}>Error: {error}</div>
  }

  if (entries.length === 0) {
    return <div style={{ padding: '1rem', color: 'var(--muted)', fontSize: '0.85em' }}>No change history yet for this control.</div>
  }

  // Group entries by timestamp (same second = same save operation)
  const groups: { time: string; email?: string; action: string; changes: AuditLogEntry[] }[] = []
  for (const entry of entries) {
    const timeKey = entry.changed_at.substring(0, 19) // truncate to second
    const last = groups[groups.length - 1]
    if (last && last.time === timeKey && last.email === entry.changed_by_email) {
      last.changes.push(entry)
    } else {
      groups.push({
        time: timeKey,
        email: entry.changed_by_email,
        action: entry.action,
        changes: [entry],
      })
    }
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '0.5rem' }}>
      <div style={{ fontSize: '0.75em', color: 'var(--muted)', padding: '0 0.25rem' }}>
        {total} change{total !== 1 ? 's' : ''} recorded
      </div>
      {groups.map((group, i) => (
        <div key={i} style={{
          border: '1px solid var(--border)',
          borderRadius: '6px',
          padding: '0.6rem 0.75rem',
          fontSize: '0.82em',
          background: 'var(--secondary)',
        }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '0.35rem' }}>
            <span style={{ fontWeight: 500, color: 'var(--text)' }}>
              {actionIcon(group.action)} {group.email || 'System'}
              <SourceBadge source={group.changes[0]?.action_source} />
            </span>
            <span style={{ color: 'var(--muted)', fontSize: '0.9em' }} title={new Date(group.time).toLocaleString()}>
              {formatTime(group.changes[0].changed_at)}
            </span>
          </div>
          {group.action === 'create' ? (
            <div style={{ color: 'var(--muted)' }}>Created scoped control</div>
          ) : (
            <div style={{ display: 'flex', flexDirection: 'column', gap: '0.2rem' }}>
              {group.changes.map((ch, j) => (
                <div key={j} style={{ color: 'var(--muted)', display: 'flex', gap: '0.3rem', flexWrap: 'wrap' }}>
                  <span style={{ color: 'var(--text)', fontWeight: 500 }}>
                    {FIELD_LABELS[ch.field_name || ''] || ch.field_name}:
                  </span>
                  <span style={{ textDecoration: 'line-through', opacity: 0.45 }}>
                    {formatValue(ch.field_name || '', ch.old_value)}
                  </span>
                  <span>{'\u2192'}</span>
                  <span style={{ color: '#059669', fontWeight: 500 }}>
                    {formatValue(ch.field_name || '', ch.new_value)}
                  </span>
                </div>
              ))}
            </div>
          )}
        </div>
      ))}
    </div>
  )
}
