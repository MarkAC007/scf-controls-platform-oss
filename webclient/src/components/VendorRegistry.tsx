import React, { useState, useEffect, useMemo } from 'react'
import type { Vendor, VendorStatus, VendorCriticality } from '../types'
import {
  VENDOR_STATUS_LABELS,
  VENDOR_CRITICALITY_LABELS,
  VENDOR_STATUS_COLORS,
  VENDOR_CRITICALITY_COLORS,
  VENDOR_RAG_COLORS,
  vendorRiskLevelToRAG
} from '../types'
import { getVendors } from '../data/apiClient'

interface VendorRegistryProps {
  organizationId: string
  onSelectVendor: (vendorId: string) => void
  onAddVendor: () => void
  onDeleteVendor: (vendor: Vendor) => void
}

/**
 * Format an ISO date string to a human-readable short date.
 * Returns a dash when the value is null or undefined.
 */
function formatDate(dateStr?: string | null): string {
  if (!dateStr) return '-'
  try {
    const d = new Date(dateStr)
    return d.toLocaleDateString('en-GB', { day: '2-digit', month: 'short', year: 'numeric' })
  } catch {
    return dateStr
  }
}

export const VendorRegistry: React.FC<VendorRegistryProps> = ({
  organizationId,
  onSelectVendor,
  onAddVendor,
  onDeleteVendor,
}) => {
  const [vendors, setVendors] = useState<Vendor[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [searchQuery, setSearchQuery] = useState('')
  const [statusFilter, setStatusFilter] = useState<VendorStatus | 'all'>('all')
  const [criticalityFilter, setCriticalityFilter] = useState<VendorCriticality | 'all'>('all')
  const [categoryFilter, setCategoryFilter] = useState<string>('all')

  // Build API filter params from the current dropdown values
  const apiFilters = useMemo(() => {
    const filters: {
      status?: VendorStatus
      criticality?: VendorCriticality
      category?: string
      search?: string
    } = {}
    if (statusFilter !== 'all') filters.status = statusFilter
    if (criticalityFilter !== 'all') filters.criticality = criticalityFilter
    if (categoryFilter !== 'all') filters.category = categoryFilter
    if (searchQuery.trim()) filters.search = searchQuery.trim()
    return filters
  }, [statusFilter, criticalityFilter, categoryFilter, searchQuery])

  useEffect(() => {
    let cancelled = false

    const loadVendors = async () => {
      setLoading(true)
      setError(null)
      try {
        const data = await getVendors(apiFilters, organizationId)
        if (!cancelled) {
          setVendors(data)
        }
      } catch (err) {
        console.error('Failed to load vendors:', err)
        if (!cancelled) {
          setError('Failed to load vendors. Please try again.')
        }
      } finally {
        if (!cancelled) {
          setLoading(false)
        }
      }
    }

    loadVendors()

    return () => {
      cancelled = true
    }
  }, [organizationId, apiFilters])

  // Client-side search filter (name only) for immediate feedback while the
  // API round-trip completes. The API also supports server-side search, but
  // we keep a fast local filter so typing feels instant.
  const filteredVendors = useMemo(() => {
    if (!searchQuery.trim()) return vendors
    const query = searchQuery.toLowerCase()
    return vendors.filter((v) => v.name.toLowerCase().includes(query))
  }, [vendors, searchQuery])

  // Derive unique categories from the current vendor set for the dropdown
  const uniqueCategories = useMemo(() => {
    const cats = new Set<string>()
    vendors.forEach((v) => {
      if (v.category) cats.add(v.category)
    })
    return Array.from(cats).sort()
  }, [vendors])

  // ----- Render helpers -----

  if (loading) {
    return (
      <div className="systems-page">
        <div className="systems-loading" style={{ textAlign: 'center', padding: '3rem 1rem' }}>
          <div
            style={{
              display: 'inline-block',
              width: '2rem',
              height: '2rem',
              border: '3px solid var(--border)',
              borderTopColor: 'var(--primary)',
              borderRadius: '50%',
              animation: 'spin 0.6s linear infinite',
            }}
          />
          <p style={{ marginTop: '1rem', color: 'var(--muted)' }}>Loading vendors...</p>
          <style>{`@keyframes spin { to { transform: rotate(360deg); } }`}</style>
        </div>
      </div>
    )
  }

  if (error) {
    return (
      <div className="systems-page">
        <div
          style={{
            textAlign: 'center',
            padding: '3rem 1rem',
            color: '#ef4444',
          }}
        >
          <p style={{ fontSize: '1.125rem', fontWeight: 600 }}>{error}</p>
          <button
            onClick={() => {
              setError(null)
              setLoading(true)
              getVendors(apiFilters, organizationId)
                .then(setVendors)
                .catch(() => setError('Failed to load vendors. Please try again.'))
                .finally(() => setLoading(false))
            }}
            style={{
              marginTop: '1rem',
              padding: '0.5rem 1.25rem',
              backgroundColor: 'var(--primary)',
              color: '#fff',
              border: 'none',
              borderRadius: '4px',
              cursor: 'pointer',
              fontWeight: 600,
            }}
          >
            Retry
          </button>
        </div>
      </div>
    )
  }

  return (
    <div className="systems-page">
      {/* Header */}
      <div className="systems-header">
        <div>
          <h1>Vendor Registry</h1>
          <p className="systems-subtitle">
            Manage third-party vendors and their risk posture for your organisation
          </p>
        </div>
        <button onClick={onAddVendor} className="systems-add-btn">
          <span style={{ fontSize: '1.25rem' }}>+</span> Add Vendor
        </button>
      </div>

      {/* Filters */}
      <div className="systems-filters">
        <div className="systems-search-wrapper">
          <input
            type="text"
            placeholder="Search vendors by name..."
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            className="systems-search-input"
          />
        </div>

        <div className="systems-filter-group">
          <label>Status:</label>
          <select
            value={statusFilter}
            onChange={(e) => setStatusFilter(e.target.value as VendorStatus | 'all')}
            className="systems-filter-select"
          >
            <option value="all">All Statuses</option>
            {(Object.keys(VENDOR_STATUS_LABELS) as VendorStatus[]).map((key) => (
              <option key={key} value={key}>
                {VENDOR_STATUS_LABELS[key]}
              </option>
            ))}
          </select>
        </div>

        <div className="systems-filter-group">
          <label>Criticality:</label>
          <select
            value={criticalityFilter}
            onChange={(e) => setCriticalityFilter(e.target.value as VendorCriticality | 'all')}
            className="systems-filter-select"
          >
            <option value="all">All Criticality Levels</option>
            {(Object.keys(VENDOR_CRITICALITY_LABELS) as VendorCriticality[]).map((key) => (
              <option key={key} value={key}>
                {VENDOR_CRITICALITY_LABELS[key]}
              </option>
            ))}
          </select>
        </div>

        <div className="systems-filter-group">
          <label>Category:</label>
          <select
            value={categoryFilter}
            onChange={(e) => setCategoryFilter(e.target.value)}
            className="systems-filter-select"
          >
            <option value="all">All Categories</option>
            {uniqueCategories.map((cat) => (
              <option key={cat} value={cat}>
                {cat}
              </option>
            ))}
          </select>
        </div>
      </div>

      {/* Vendor List */}
      {filteredVendors.length === 0 ? (
        <div className="systems-empty-state">
          <div className="systems-empty-icon">🏢</div>
          <h3>No Vendors Found</h3>
          <p>
            {vendors.length === 0
              ? 'Add your first vendor to start tracking third-party risk.'
              : 'No vendors match the current filters. Try adjusting your search or filter criteria.'}
          </p>
          {vendors.length === 0 && (
            <button
              onClick={onAddVendor}
              style={{
                marginTop: '1rem',
                padding: '0.75rem 1.5rem',
                backgroundColor: 'var(--primary)',
                color: '#fff',
                border: 'none',
                borderRadius: '4px',
                cursor: 'pointer',
                fontWeight: 600,
              }}
            >
              Add Your First Vendor
            </button>
          )}
        </div>
      ) : (
        <div className="systems-list-container">
          {/* Table Header */}
          <div
            className="systems-table-header"
            style={{
              gridTemplateColumns: '2fr 1fr 1fr 1fr 0.9fr 0.9fr 1fr 1.25fr 0.5fr',
            }}
          >
            <div>Name</div>
            <div>Category</div>
            <div>Status</div>
            <div>Criticality</div>
            <div>Risk</div>
            <div>Review</div>
            <div>Contract End Date</div>
            <div>Contact</div>
            <div></div>
          </div>

          {/* Vendor Rows */}
          {filteredVendors.map((vendor) => {
            const statusColor = VENDOR_STATUS_COLORS[vendor.status] || '#6b7280'
            const statusLabel = VENDOR_STATUS_LABELS[vendor.status] || vendor.status
            const criticalityColor = VENDOR_CRITICALITY_COLORS[vendor.criticality] || '#6b7280'
            const criticalityLabel = VENDOR_CRITICALITY_LABELS[vendor.criticality] || vendor.criticality
            const rag = vendorRiskLevelToRAG(vendor.risk_level)
            const ragColor = rag ? VENDOR_RAG_COLORS[rag] : null
            const reviewStatus = vendor.review_status

            return (
              <div
                key={vendor.id}
                className="systems-table-row"
                style={{
                  gridTemplateColumns: '2fr 1fr 1fr 1fr 0.9fr 0.9fr 1fr 1.25fr 0.5fr',
                  cursor: 'pointer',
                }}
                onClick={() => onSelectVendor(vendor.id)}
              >
                {/* Name */}
                <div>
                  <div className="systems-name-link">{vendor.name}</div>
                  {vendor.description && (
                    <div className="systems-description">
                      {vendor.description.length > 60
                        ? `${vendor.description.substring(0, 60)}...`
                        : vendor.description}
                    </div>
                  )}
                </div>

                {/* Category */}
                <div className="systems-vendor">{vendor.category || '-'}</div>

                {/* Status Badge */}
                <div>
                  <span
                    className="systems-badge"
                    style={{
                      backgroundColor: statusColor + '1a',
                      color: statusColor,
                      border: `1px solid ${statusColor}40`,
                      padding: '0.2rem 0.6rem',
                      borderRadius: '4px',
                      fontSize: '0.8rem',
                      fontWeight: 600,
                      whiteSpace: 'nowrap',
                    }}
                  >
                    {statusLabel}
                  </span>
                </div>

                {/* Criticality Badge */}
                <div>
                  <span
                    className="systems-badge"
                    style={{
                      backgroundColor: criticalityColor + '1a',
                      color: criticalityColor,
                      border: `1px solid ${criticalityColor}40`,
                      padding: '0.2rem 0.6rem',
                      borderRadius: '4px',
                      fontSize: '0.8rem',
                      fontWeight: 600,
                      whiteSpace: 'nowrap',
                    }}
                  >
                    {criticalityLabel}
                  </span>
                </div>

                {/* Risk: single score + RAG pill */}
                <div>
                  {vendor.risk_score != null && ragColor ? (
                    <span
                      style={{
                        display: 'inline-flex',
                        alignItems: 'center',
                        gap: '0.25rem',
                        padding: '0.2rem 0.6rem',
                        borderRadius: '9999px',
                        fontSize: '0.8rem',
                        fontWeight: 600,
                        whiteSpace: 'nowrap',
                        backgroundColor: ragColor + '1a',
                        color: ragColor,
                        border: `1px solid ${ragColor}40`,
                      }}
                    >
                      {vendor.risk_score} · {rag}
                    </span>
                  ) : vendor.risk_score != null ? (
                    <span style={{ fontWeight: 600, color: 'var(--text)' }}>{vendor.risk_score}</span>
                  ) : (
                    <span style={{ color: 'var(--muted)' }}>-</span>
                  )}
                </div>

                {/* Review due badge */}
                <div>
                  {reviewStatus === 'overdue' || reviewStatus === 'due_soon' ? (
                    <span
                      style={{
                        display: 'inline-block',
                        padding: '0.2rem 0.6rem',
                        borderRadius: '4px',
                        fontSize: '0.8rem',
                        fontWeight: 600,
                        whiteSpace: 'nowrap',
                        backgroundColor: (reviewStatus === 'overdue' ? '#ef4444' : '#f59e0b') + '1a',
                        color: reviewStatus === 'overdue' ? '#ef4444' : '#f59e0b',
                        border: `1px solid ${(reviewStatus === 'overdue' ? '#ef4444' : '#f59e0b')}40`,
                      }}
                    >
                      {reviewStatus === 'overdue' ? 'Overdue' : 'Due soon'}
                    </span>
                  ) : vendor.next_review_date ? (
                    <span style={{ color: 'var(--muted)', fontSize: '0.8rem', whiteSpace: 'nowrap' }}>
                      {formatDate(vendor.next_review_date)}
                    </span>
                  ) : (
                    <span style={{ color: 'var(--muted)' }}>-</span>
                  )}
                </div>

                {/* Contract End Date */}
                <div style={{ color: 'var(--text)', fontSize: '0.875rem' }}>
                  {formatDate(vendor.contract_end_date)}
                </div>

                {/* Contact */}
                <div style={{ fontSize: '0.875rem' }}>
                  {vendor.contact_name ? (
                    <div>
                      <div style={{ fontWeight: 500, color: 'var(--text)' }}>{vendor.contact_name}</div>
                      {vendor.contact_email && (
                        <div style={{ color: 'var(--muted)', fontSize: '0.8rem' }}>
                          {vendor.contact_email}
                        </div>
                      )}
                    </div>
                  ) : (
                    <span style={{ color: 'var(--muted)' }}>-</span>
                  )}
                </div>

                {/* Delete action */}
                <div style={{ display: 'flex', justifyContent: 'center' }}>
                  <button
                    onClick={(e) => {
                      e.stopPropagation()
                      onDeleteVendor(vendor)
                    }}
                    title="Delete vendor"
                    style={{
                      background: 'none',
                      border: 'none',
                      cursor: 'pointer',
                      padding: '4px 8px',
                      borderRadius: '4px',
                      color: 'var(--muted)',
                      fontSize: '0.875rem',
                      transition: 'color 0.15s',
                    }}
                    onMouseEnter={(e) => (e.currentTarget.style.color = '#ef4444')}
                    onMouseLeave={(e) => (e.currentTarget.style.color = 'var(--muted)')}
                  >
                    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                      <polyline points="3 6 5 6 21 6" />
                      <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2" />
                    </svg>
                  </button>
                </div>
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}

export default VendorRegistry
