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
import FilterSidebar, { FilterGroup, FilterSelect } from './explorer/FilterSidebar'
import ListToolbar from './explorer/ListToolbar'
import ExplorerListRow, { RowMeta } from './explorer/ListRow'

interface VendorRegistryProps {
  organizationId: string
  onSelectVendor: (vendorId: string) => void
  onAddVendor: () => void
  onDeleteVendor: (vendor: Vendor) => void
  onFilteredListChange?: (list: Vendor[]) => void
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

// Status filter options
const STATUS_OPTIONS: { value: string; label: string }[] = [
  { value: 'all', label: 'All Statuses' },
  ...(Object.keys(VENDOR_STATUS_LABELS) as VendorStatus[]).map((key) => ({
    value: key,
    label: VENDOR_STATUS_LABELS[key],
  })),
]

// Criticality filter options
const CRITICALITY_OPTIONS: { value: string; label: string }[] = [
  { value: 'all', label: 'All Criticality Levels' },
  ...(Object.keys(VENDOR_CRITICALITY_LABELS) as VendorCriticality[]).map((key) => ({
    value: key,
    label: VENDOR_CRITICALITY_LABELS[key],
  })),
]

export const VendorRegistry: React.FC<VendorRegistryProps> = ({
  organizationId,
  onSelectVendor,
  onAddVendor,
  onDeleteVendor,
  onFilteredListChange,
}) => {
  const [vendors, setVendors] = useState<Vendor[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [searchQuery, setSearchQuery] = useState('')
  const [statusFilter, setStatusFilter] = useState<VendorStatus | 'all'>('all')
  const [criticalityFilter, setCriticalityFilter] = useState<VendorCriticality | 'all'>('all')
  const [categoryFilter, setCategoryFilter] = useState<string>('all')
  const [filtersCollapsed, setFiltersCollapsed] = useState(false)

  // Build API filter params from the current sidebar filter values.
  // Search is handled client-side only for immediate feedback — it is NOT
  // included here to avoid an API round-trip on every keystroke.
  const apiFilters = useMemo(() => {
    const filters: {
      status?: VendorStatus
      criticality?: VendorCriticality
      category?: string
    } = {}
    if (statusFilter !== 'all') filters.status = statusFilter
    if (criticalityFilter !== 'all') filters.criticality = criticalityFilter
    if (categoryFilter !== 'all') filters.category = categoryFilter
    return filters
  }, [statusFilter, criticalityFilter, categoryFilter])

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

  const categoryOptions: { value: string; label: string }[] = useMemo(() => [
    { value: 'all', label: 'All Categories' },
    ...uniqueCategories.map((cat) => ({ value: cat, label: cat })),
  ], [uniqueCategories])

  // Notify parent of the current filtered list (for pager navigation in VendorDetailPage)
  useEffect(() => {
    onFilteredListChange?.(filteredVendors)
  }, [filteredVendors, onFilteredListChange])

  // ----- Render helpers -----

  if (loading) {
    return (
      <div className="vendor-explorer-page">
        <div className="vendor-explorer-loading">
          <div className="vendor-explorer-spinner" aria-hidden="true" />
          <p>Loading vendors...</p>
        </div>
      </div>
    )
  }

  if (error) {
    return (
      <div className="vendor-explorer-page">
        <div className="vendor-explorer-error">
          <p>{error}</p>
          <button
            onClick={() => {
              setError(null)
              setLoading(true)
              getVendors(apiFilters, organizationId)
                .then(setVendors)
                .catch(() => setError('Failed to load vendors. Please try again.'))
                .finally(() => setLoading(false))
            }}
            className="vendor-explorer-retry-btn"
          >
            Retry
          </button>
        </div>
      </div>
    )
  }

  return (
    <div className="vendor-explorer-page">
      <FilterSidebar
        collapsed={filtersCollapsed}
        onToggleCollapsed={() => setFiltersCollapsed((c) => !c)}
        aria-label="Vendor filters"
      >
        <FilterGroup label="STATUS">
          <FilterSelect
            value={statusFilter}
            onChange={(v) => setStatusFilter(v as VendorStatus | 'all')}
            options={STATUS_OPTIONS}
          />
        </FilterGroup>

        <FilterGroup label="CRITICALITY">
          <FilterSelect
            value={criticalityFilter}
            onChange={(v) => setCriticalityFilter(v as VendorCriticality | 'all')}
            options={CRITICALITY_OPTIONS}
          />
        </FilterGroup>

        <FilterGroup label="CATEGORY">
          <FilterSelect
            value={categoryFilter}
            onChange={(v) => setCategoryFilter(v)}
            options={categoryOptions}
          />
        </FilterGroup>
      </FilterSidebar>

      <div className="vendor-explorer-body">
        <ListToolbar
          search={searchQuery}
          onSearchChange={setSearchQuery}
          searchPlaceholder="Search vendors by name…"
          count={
            <span className="vendor-explorer-count">
              {filteredVendors.length} vendor{filteredVendors.length !== 1 ? 's' : ''}
            </span>
          }
          actions={
            <button onClick={onAddVendor} className="vendor-explorer-add-btn">
              + Add Vendor
            </button>
          }
        />

        <div className="vendor-explorer-rows">
          {filteredVendors.length === 0 ? (
            <div className="vendor-explorer-empty">
              <h3>No Vendors Found</h3>
              <p>
                {vendors.length === 0
                  ? 'Add your first vendor to start tracking third-party risk.'
                  : 'No vendors match the current filters. Try adjusting your search or filter criteria.'}
              </p>
              {vendors.length === 0 && (
                <button onClick={onAddVendor} className="vendor-explorer-add-btn">
                  Add Your First Vendor
                </button>
              )}
            </div>
          ) : (
            filteredVendors.map((vendor) => {
              const statusColor = VENDOR_STATUS_COLORS[vendor.status] || 'var(--muted)'
              const statusLabel = VENDOR_STATUS_LABELS[vendor.status] || vendor.status
              const criticalityColor = VENDOR_CRITICALITY_COLORS[vendor.criticality] || 'var(--muted)'
              const criticalityLabel = VENDOR_CRITICALITY_LABELS[vendor.criticality] || vendor.criticality
              const rag = vendorRiskLevelToRAG(vendor.risk_level)
              const ragColor = rag ? VENDOR_RAG_COLORS[rag] : null
              const reviewStatus = vendor.review_status

              return (
                <ExplorerListRow
                  key={vendor.id}
                  title={vendor.name}
                  description={vendor.description ?? undefined}
                  onClick={() => onSelectVendor(vendor.id)}
                >
                  {/* Category chip */}
                  {vendor.category && (
                    <RowMeta>
                      <span className="vendor-chip">{vendor.category}</span>
                    </RowMeta>
                  )}

                  {/* Status badge */}
                  <RowMeta>
                    <span
                      className="vendor-badge"
                      style={{
                        backgroundColor: statusColor + '1a',
                        color: statusColor,
                        border: `1px solid ${statusColor}40`,
                      }}
                    >
                      {statusLabel}
                    </span>
                  </RowMeta>

                  {/* Criticality badge */}
                  <RowMeta>
                    <span
                      className="vendor-badge"
                      style={{
                        backgroundColor: criticalityColor + '1a',
                        color: criticalityColor,
                        border: `1px solid ${criticalityColor}40`,
                      }}
                    >
                      {criticalityLabel}
                    </span>
                  </RowMeta>

                  {/* Risk score + RAG pill */}
                  <RowMeta>
                    {vendor.risk_score != null && ragColor ? (
                      <span
                        className="vendor-rag-pill"
                        style={{
                          backgroundColor: ragColor + '1a',
                          color: ragColor,
                          border: `1px solid ${ragColor}40`,
                        }}
                      >
                        {vendor.risk_score} · {rag}
                      </span>
                    ) : vendor.risk_score != null ? (
                      <span className="vendor-risk-plain">{vendor.risk_score}</span>
                    ) : (
                      <span className="vendor-meta-dash">-</span>
                    )}
                  </RowMeta>

                  {/* Review status */}
                  <RowMeta>
                    {reviewStatus === 'overdue' || reviewStatus === 'due_soon' ? (
                      <span
                        className="vendor-badge"
                        style={{
                          backgroundColor: (reviewStatus === 'overdue' ? 'var(--destructive)' : 'var(--warning)') + '1a',
                          color: reviewStatus === 'overdue' ? 'var(--destructive)' : 'var(--warning)',
                          border: `1px solid ${reviewStatus === 'overdue' ? 'var(--destructive)' : 'var(--warning)'}40`,
                        }}
                      >
                        {reviewStatus === 'overdue' ? 'Overdue' : 'Due soon'}
                      </span>
                    ) : vendor.next_review_date ? (
                      <span className="vendor-meta-muted">{formatDate(vendor.next_review_date)}</span>
                    ) : (
                      <span className="vendor-meta-dash">-</span>
                    )}
                  </RowMeta>

                  {/* Contract end date */}
                  <RowMeta>
                    <span className="vendor-meta-text">{formatDate(vendor.contract_end_date)}</span>
                  </RowMeta>

                  {/* Contact */}
                  <RowMeta>
                    {vendor.contact_name ? (
                      <span className="vendor-contact">
                        <span className="vendor-contact-name">{vendor.contact_name}</span>
                        {vendor.contact_email && (
                          <span className="vendor-contact-email">{vendor.contact_email}</span>
                        )}
                      </span>
                    ) : (
                      <span className="vendor-meta-dash">-</span>
                    )}
                  </RowMeta>

                  {/* Delete action — outside row click with stopPropagation */}
                  <RowMeta>
                    <button
                      onClick={(e) => {
                        e.stopPropagation()
                        onDeleteVendor(vendor)
                      }}
                      title="Delete vendor"
                      aria-label={`Delete ${vendor.name}`}
                      className="vendor-delete-btn"
                    >
                      <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
                        <polyline points="3 6 5 6 21 6" />
                        <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2" />
                      </svg>
                    </button>
                  </RowMeta>
                </ExplorerListRow>
              )
            })
          )}
        </div>
      </div>
    </div>
  )
}

export default VendorRegistry
