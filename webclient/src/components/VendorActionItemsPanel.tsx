/**
 * VendorActionItemsPanel -- Action items display for a vendor (DPSIA Enhancement).
 *
 * Shows a table of action items with priority/status badges, status filter,
 * and inline status updates.
 */
import { useState, useEffect, useCallback } from 'react'
import type { VendorActionItem, ActionItemStatus } from '../types'
import { ACTION_PRIORITY_COLORS, ACTION_STATUS_COLORS } from '../types'
import {
  getVendorActionItems,
  updateVendorActionItem,
} from '../data/apiClient'

interface VendorActionItemsPanelProps {
  organizationId: string
  vendorId: string
}

const STATUS_OPTIONS: { value: ActionItemStatus | 'all'; label: string }[] = [
  { value: 'all', label: 'All' },
  { value: 'open', label: 'Open' },
  { value: 'in_progress', label: 'In Progress' },
  { value: 'completed', label: 'Completed' },
  { value: 'cancelled', label: 'Cancelled' },
]

const STATUS_LABELS: Record<ActionItemStatus, string> = {
  open: 'Open',
  in_progress: 'In Progress',
  completed: 'Completed',
  cancelled: 'Cancelled',
}

const PRIORITY_LABELS: Record<string, string> = {
  critical: 'Critical',
  high: 'High',
  medium: 'Medium',
  low: 'Low',
}

export default function VendorActionItemsPanel({
  organizationId,
  vendorId,
}: VendorActionItemsPanelProps) {
  const [items, setItems] = useState<VendorActionItem[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [statusFilter, setStatusFilter] = useState<ActionItemStatus | 'all'>('all')
  const [updatingId, setUpdatingId] = useState<string | null>(null)

  // --------------------------------------------------
  // Load action items
  // --------------------------------------------------
  const loadItems = useCallback(async () => {
    setLoading(true)
    try {
      const data = await getVendorActionItems(vendorId, organizationId)
      setItems(data)
      setError(null)
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : 'Failed to load action items'
      if (!message.includes('404')) {
        setError(message)
      }
    } finally {
      setLoading(false)
    }
  }, [vendorId, organizationId])

  useEffect(() => {
    loadItems()
  }, [loadItems])

  // --------------------------------------------------
  // Inline status update
  // --------------------------------------------------
  const handleStatusChange = async (item: VendorActionItem, newStatus: ActionItemStatus) => {
    setUpdatingId(item.id)
    try {
      const updated = await updateVendorActionItem(
        vendorId,
        item.id,
        { status: newStatus },
        organizationId
      )
      setItems((prev) =>
        prev.map((i) => (i.id === item.id ? { ...i, ...updated } : i))
      )
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : 'Failed to update status'
      setError(message)
    } finally {
      setUpdatingId(null)
    }
  }

  // --------------------------------------------------
  // Filter items
  // --------------------------------------------------
  const filteredItems =
    statusFilter === 'all'
      ? items
      : items.filter((i) => i.status === statusFilter)

  const formatDate = (dateStr: string | null | undefined): string => {
    if (!dateStr) return '-'
    try {
      return new Date(dateStr).toLocaleDateString('en-GB', {
        day: 'numeric',
        month: 'short',
        year: 'numeric',
      })
    } catch {
      return dateStr
    }
  }

  const isOverdue = (item: VendorActionItem): boolean => {
    if (!item.due_date || item.status === 'completed' || item.status === 'cancelled') return false
    return new Date(item.due_date) < new Date()
  }

  // --------------------------------------------------
  // Render
  // --------------------------------------------------
  return (
    <div>
      {/* Section header */}
      <div
        style={{
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center',
          marginBottom: '1rem',
          gap: '1rem',
          flexWrap: 'wrap',
        }}
      >
        <h3 style={{ margin: 0, fontSize: '1rem', fontWeight: 600, color: 'var(--text)' }}>
          Action Items ({items.length})
        </h3>

        {/* Status filter */}
        <div style={{ display: 'flex', gap: '0.25rem' }}>
          {STATUS_OPTIONS.map((opt) => (
            <button
              key={opt.value}
              onClick={() => setStatusFilter(opt.value)}
              style={{
                padding: '0.25rem 0.625rem',
                borderRadius: '4px',
                border: '1px solid var(--border)',
                backgroundColor: statusFilter === opt.value ? 'var(--primary)' : 'var(--card)',
                color: statusFilter === opt.value ? '#ffffff' : 'var(--text)',
                cursor: 'pointer',
                fontSize: '0.75rem',
                fontWeight: 500,
              }}
            >
              {opt.label}
            </button>
          ))}
        </div>
      </div>

      {/* Error banner */}
      {error && (
        <div
          style={{
            padding: '0.75rem 1rem',
            backgroundColor: 'var(--destructive-bg, #fef2f2)',
            border: '1px solid var(--destructive-border, #fecaca)',
            borderRadius: '8px',
            color: 'var(--destructive)',
            marginBottom: '1rem',
            fontSize: '0.875rem',
          }}
        >
          {error}
          <button
            onClick={() => setError(null)}
            style={{
              marginLeft: '0.5rem',
              background: 'none',
              border: 'none',
              color: 'var(--destructive)',
              cursor: 'pointer',
              fontWeight: 600,
            }}
          >
            Dismiss
          </button>
        </div>
      )}

      {/* Loading */}
      {loading && items.length === 0 && (
        <div style={{ textAlign: 'center', padding: '1rem' }}>
          <div
            style={{
              display: 'inline-block',
              width: '1.5rem',
              height: '1.5rem',
              border: '2px solid var(--border)',
              borderTopColor: 'var(--primary)',
              borderRadius: '50%',
              animation: 'spin 0.8s linear infinite',
            }}
          />
          <p style={{ color: 'var(--muted)', fontSize: '0.8rem', marginTop: '0.5rem' }}>
            Loading action items...
          </p>
          <style>{`@keyframes spin { to { transform: rotate(360deg); } }`}</style>
        </div>
      )}

      {/* Empty state */}
      {!loading && items.length === 0 && !error && (
        <div
          style={{
            padding: '2rem',
            textAlign: 'center',
            color: 'var(--muted)',
            backgroundColor: 'var(--card)',
            borderRadius: '8px',
            border: '1px dashed var(--border)',
            fontSize: '0.875rem',
          }}
        >
          No action items yet. Action items are generated automatically during
          risk calculations and report generation, or can be added manually.
        </div>
      )}

      {/* No results for filter */}
      {!loading && items.length > 0 && filteredItems.length === 0 && (
        <div
          style={{
            padding: '1.5rem',
            textAlign: 'center',
            color: 'var(--muted)',
            fontSize: '0.875rem',
          }}
        >
          No action items match the selected filter.
        </div>
      )}

      {/* Action items table */}
      {filteredItems.length > 0 && (
        <div style={{ overflowX: 'auto' }}>
          <table
            style={{
              width: '100%',
              borderCollapse: 'collapse',
              fontSize: '0.875rem',
            }}
          >
            <thead>
              <tr
                style={{
                  borderBottom: '2px solid var(--border)',
                  textAlign: 'left',
                }}
              >
                <th style={{ padding: '0.5rem 0.75rem', fontWeight: 600, color: 'var(--text)' }}>Title</th>
                <th style={{ padding: '0.5rem 0.75rem', fontWeight: 600, color: 'var(--text)' }}>Priority</th>
                <th style={{ padding: '0.5rem 0.75rem', fontWeight: 600, color: 'var(--text)' }}>Owner</th>
                <th style={{ padding: '0.5rem 0.75rem', fontWeight: 600, color: 'var(--text)' }}>Due Date</th>
                <th style={{ padding: '0.5rem 0.75rem', fontWeight: 600, color: 'var(--text)' }}>Status</th>
              </tr>
            </thead>
            <tbody>
              {filteredItems.map((item) => (
                <tr
                  key={item.id}
                  style={{ borderBottom: '1px solid var(--border)' }}
                >
                  <td style={{ padding: '0.5rem 0.75rem', color: 'var(--text)' }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: '0.375rem' }}>
                      <span style={{ fontWeight: 500 }}>{item.title}</span>
                      {item.auto_generated && (
                        <span
                          style={{
                            display: 'inline-block',
                            padding: '1px 6px',
                            borderRadius: '4px',
                            fontSize: '0.65rem',
                            fontWeight: 600,
                            color: '#6b21a8',
                            backgroundColor: '#f3e8ff',
                            flexShrink: 0,
                          }}
                        >
                          Auto
                        </span>
                      )}
                    </div>
                    {item.description && (
                      <div
                        style={{
                          color: 'var(--muted)',
                          fontSize: '0.75rem',
                          marginTop: '0.125rem',
                          maxWidth: '300px',
                          overflow: 'hidden',
                          textOverflow: 'ellipsis',
                          whiteSpace: 'nowrap',
                        }}
                        title={item.description}
                      >
                        {item.description}
                      </div>
                    )}
                  </td>
                  <td style={{ padding: '0.5rem 0.75rem' }}>
                    <span
                      style={{
                        display: 'inline-block',
                        padding: '2px 10px',
                        borderRadius: '9999px',
                        fontSize: '0.75rem',
                        fontWeight: 600,
                        color: '#ffffff',
                        backgroundColor: ACTION_PRIORITY_COLORS[item.priority],
                      }}
                    >
                      {PRIORITY_LABELS[item.priority] || item.priority}
                    </span>
                  </td>
                  <td style={{ padding: '0.5rem 0.75rem', color: 'var(--text)' }}>
                    {item.owner_name || <span style={{ color: 'var(--muted)' }}>-</span>}
                  </td>
                  <td style={{ padding: '0.5rem 0.75rem' }}>
                    <span
                      style={{
                        color: isOverdue(item) ? '#ef4444' : 'var(--text)',
                        fontWeight: isOverdue(item) ? 600 : 400,
                      }}
                    >
                      {formatDate(item.due_date)}
                    </span>
                    {isOverdue(item) && (
                      <span
                        style={{
                          display: 'block',
                          fontSize: '0.65rem',
                          color: '#ef4444',
                          fontWeight: 600,
                        }}
                      >
                        Overdue
                      </span>
                    )}
                  </td>
                  <td style={{ padding: '0.5rem 0.75rem' }}>
                    <select
                      value={item.status}
                      disabled={updatingId === item.id}
                      onChange={(e) =>
                        handleStatusChange(item, e.target.value as ActionItemStatus)
                      }
                      style={{
                        padding: '0.25rem 0.5rem',
                        borderRadius: '4px',
                        border: '1px solid var(--border)',
                        backgroundColor: ACTION_STATUS_COLORS[item.status] + '20',
                        color: 'var(--text)',
                        fontSize: '0.75rem',
                        cursor: updatingId === item.id ? 'wait' : 'pointer',
                        fontWeight: 500,
                      }}
                    >
                      {(Object.keys(STATUS_LABELS) as ActionItemStatus[]).map((s) => (
                        <option key={s} value={s}>
                          {STATUS_LABELS[s]}
                        </option>
                      ))}
                    </select>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
