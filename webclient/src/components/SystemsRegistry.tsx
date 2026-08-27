import React, { useState, useEffect, useMemo, useRef } from 'react'
import { getSystems, deleteSystem } from '../data/apiClient'
import type { System, SystemType, SystemStatus, CollectionInterfacesFile, CollectionInterface } from '../types'
import FilterSidebar, { FilterGroup, FilterSelect } from './explorer/FilterSidebar'
import ListToolbar from './explorer/ListToolbar'
import ExplorerListRow, { RowMeta } from './explorer/ListRow'

interface SystemsRegistryProps {
  organizationId?: string
  collectionInterfaces?: CollectionInterfacesFile
  onAddSystem: () => void
  onEditSystem: (system: System) => void
  onViewSystem: (system: System) => void
  /** Called whenever the filtered list changes — used by SystemsManagement for pager. */
  onFilteredListChange?: (systems: System[]) => void
}

// Map platform SystemType to catalog CatalogSystemType
const systemTypeToCatalogTypes: Record<SystemType, string[]> = {
  cloud_provider: ['cloud_provider'],
  identity_provider: ['identity_provider', 'iga_platform', 'pam_tool'],
  ticketing: ['ticketing', 'cmdb'],
  logging: ['siem', 'logging'],
  security_tool: ['security_tool', 'vulnerability_scanner', 'siem'],
  code_repository: ['code_repository'],
  document_management: ['document_management'],
  endpoint_management: ['mdm_tool', 'security_tool'],
  vulnerability_management: ['vulnerability_scanner', 'security_tool'],
  email_security: ['security_tool'],
  security_awareness: ['hr_system'],
  password_manager: ['pam_tool', 'security_tool'],
  communication: ['document_management'],
  hr_system: ['hr_system'],
  custom: [],
}

// Get compatible collection interfaces for a system type
function getCompatibleInterfaces(
  systemType: SystemType,
  collectionInterfaces?: CollectionInterfacesFile
): { id: string; interface: CollectionInterface }[] {
  if (!collectionInterfaces) return []

  const catalogTypes = systemTypeToCatalogTypes[systemType] || []
  if (catalogTypes.length === 0) return []

  return Object.entries(collectionInterfaces)
    .filter(([_, ci]) => ci.system_types?.some(st => catalogTypes.includes(st)))
    .map(([id, ci]) => ({ id, interface: ci }))
}

// System type display configuration
const systemTypeConfig: Record<SystemType, { label: string; color: string; bg: string }> = {
  cloud_provider: { label: 'Cloud Provider', color: '#0288d1', bg: '#e1f5fe' },
  identity_provider: { label: 'Identity Provider', color: '#7b1fa2', bg: '#f3e5f5' },
  ticketing: { label: 'Ticketing', color: '#f57c00', bg: '#fff3e0' },
  logging: { label: 'Logging', color: '#388e3c', bg: '#e8f5e9' },
  security_tool: { label: 'Security Tool', color: '#d32f2f', bg: '#ffebee' },
  code_repository: { label: 'Code Repository', color: '#5d4037', bg: '#efebe9' },
  document_management: { label: 'Document Mgmt', color: '#1976d2', bg: '#e3f2fd' },
  endpoint_management: { label: 'Endpoint Mgmt', color: '#00838f', bg: '#e0f7fa' },
  vulnerability_management: { label: 'Vulnerability Mgmt', color: '#c62828', bg: '#fce4ec' },
  email_security: { label: 'Email Security', color: '#6a1b9a', bg: '#ede7f6' },
  security_awareness: { label: 'Security Awareness', color: '#ef6c00', bg: '#fff8e1' },
  password_manager: { label: 'Password Manager', color: '#283593', bg: '#e8eaf6' },
  communication: { label: 'Communication', color: '#00695c', bg: '#e0f2f1' },
  hr_system: { label: 'HR System', color: '#ad1457', bg: '#fce4ec' },
  custom: { label: 'Custom', color: '#666', bg: '#f5f5f5' },
}

// Status display configuration
const statusConfig: Record<SystemStatus, { label: string; color: string; bg: string }> = {
  active: { label: 'Active', color: '#388e3c', bg: '#e8f5e9' },
  inactive: { label: 'Inactive', color: '#f57c00', bg: '#fff3e0' },
  deprecated: { label: 'Deprecated', color: '#d32f2f', bg: '#ffebee' },
}

// Type filter options
const TYPE_OPTIONS: { value: string; label: string }[] = [
  { value: 'all', label: 'All Types' },
  ...Object.entries(systemTypeConfig).map(([key, config]) => ({
    value: key,
    label: config.label,
  })),
]

// Status filter options
const STATUS_OPTIONS: { value: string; label: string }[] = [
  { value: 'all', label: 'All Statuses' },
  ...Object.entries(statusConfig).map(([key, config]) => ({
    value: key,
    label: config.label,
  })),
]

export const SystemsRegistry: React.FC<SystemsRegistryProps> = ({
  organizationId,
  collectionInterfaces,
  onAddSystem,
  onEditSystem,
  onViewSystem,
  onFilteredListChange,
}) => {
  const [systems, setSystems] = useState<System[]>([])
  const [loading, setLoading] = useState(true)
  const [typeFilter, setTypeFilter] = useState<string>('all')
  const [statusFilter, setStatusFilter] = useState<string>('all')
  const [searchQuery, setSearchQuery] = useState('')
  const [deleteConfirm, setDeleteConfirm] = useState<string | null>(null)
  const [filtersCollapsed, setFiltersCollapsed] = useState(false)

  useEffect(() => {
    loadSystems()
  }, [organizationId])

  const loadSystems = async () => {
    setLoading(true)
    try {
      const data = await getSystems(organizationId)
      setSystems(data)
    } catch (error) {
      console.error('Failed to load systems:', error)
    } finally {
      setLoading(false)
    }
  }

  const handleDeleteSystem = async (systemId: string) => {
    try {
      await deleteSystem(systemId, organizationId)
      setDeleteConfirm(null)
      await loadSystems()
    } catch (error) {
      console.error('Failed to delete system:', error)
      alert('Failed to delete system. It may have associated evidence capabilities.')
    }
  }

  // Filter systems
  const filteredSystems = useMemo(() => systems.filter(system => {
    // Type filter
    if (typeFilter !== 'all' && system.system_type !== typeFilter) {
      return false
    }
    // Status filter
    if (statusFilter !== 'all' && system.status !== statusFilter) {
      return false
    }
    // Search query
    if (searchQuery) {
      const query = searchQuery.toLowerCase()
      return (
        system.name.toLowerCase().includes(query) ||
        (system.vendor?.toLowerCase().includes(query)) ||
        (system.description?.toLowerCase().includes(query)) ||
        (system.category?.toLowerCase().includes(query))
      )
    }
    return true
  }), [systems, typeFilter, statusFilter, searchQuery])

  // Notify parent whenever filteredSystems changes (for pager in SystemDetailPage)
  const onFilteredListChangeRef = useRef(onFilteredListChange)
  onFilteredListChangeRef.current = onFilteredListChange
  useEffect(() => {
    onFilteredListChangeRef.current?.(filteredSystems)
  }, [filteredSystems])

  // Stats — derived from full system set (not filtered)
  const stats = useMemo(() => ({
    total: systems.length,
    active: systems.filter(s => s.status === 'active').length,
    byType: Object.entries(
      systems.reduce((acc, s) => {
        acc[s.system_type] = (acc[s.system_type] || 0) + 1
        return acc
      }, {} as Record<string, number>)
    ),
  }), [systems])

  return (
    <div className="system-explorer-page">
      {/* Stats cards — cheaply client-side derivable */}
      <div className="system-explorer-stats">
        <div className="system-stat-card">
          <div className="system-stat-value system-stat-value--blue">{stats.total}</div>
          <div className="system-stat-label">Total Systems</div>
        </div>
        <div className="system-stat-card">
          <div className="system-stat-value system-stat-value--green">{stats.active}</div>
          <div className="system-stat-label">Active</div>
        </div>
        {stats.byType.slice(0, 3).map(([type, count]) => (
          <div key={type} className="system-stat-card">
            <div
              className="system-stat-value"
              style={{ color: systemTypeConfig[type as SystemType]?.color || '#666' }}
            >
              {count}
            </div>
            <div className="system-stat-label">
              {systemTypeConfig[type as SystemType]?.label || type}
            </div>
          </div>
        ))}
      </div>

      <div className="system-explorer-layout">
        <FilterSidebar
          collapsed={filtersCollapsed}
          onToggleCollapsed={() => setFiltersCollapsed((c) => !c)}
          aria-label="System filters"
        >
          <FilterGroup label="TYPE">
            <FilterSelect
              value={typeFilter}
              onChange={setTypeFilter}
              options={TYPE_OPTIONS}
            />
          </FilterGroup>

          <FilterGroup label="STATUS">
            <FilterSelect
              value={statusFilter}
              onChange={setStatusFilter}
              options={STATUS_OPTIONS}
            />
          </FilterGroup>
        </FilterSidebar>

        <div className="system-explorer-body">
          <ListToolbar
            search={searchQuery}
            onSearchChange={setSearchQuery}
            searchPlaceholder="Search systems…"
            count={
              <span className="system-explorer-count">
                {filteredSystems.length} system{filteredSystems.length !== 1 ? 's' : ''}
              </span>
            }
            actions={
              <button onClick={onAddSystem} className="system-explorer-add-btn">
                + Add System
              </button>
            }
          />

          <div className="system-explorer-rows">
            {loading ? (
              <div className="system-explorer-loading">Loading systems...</div>
            ) : filteredSystems.length === 0 ? (
              <div className="system-explorer-empty">
                <h3>No Systems Found</h3>
                <p>
                  {systems.length === 0
                    ? 'Add your first system to start tracking evidence collection capabilities.'
                    : 'Try adjusting the filters to see more results.'}
                </p>
                {systems.length === 0 && (
                  <button onClick={onAddSystem} className="system-explorer-add-btn">
                    Add Your First System
                  </button>
                )}
              </div>
            ) : (
              filteredSystems.map((system) => {
                const typeInfo = systemTypeConfig[system.system_type] || systemTypeConfig.custom
                const statusInfo = statusConfig[system.status] || statusConfig.active
                const compatibleInterfaces = getCompatibleInterfaces(system.system_type, collectionInterfaces)

                return (
                  <ExplorerListRow
                    key={system.id}
                    title={system.name}
                    description={
                      [system.description, system.category ? `Category: ${system.category}` : null]
                        .filter(Boolean)
                        .join(' · ') || undefined
                    }
                    onClick={() => onViewSystem(system)}
                  >
                    {/* Type badge — existing color coding from systemTypeConfig */}
                    <RowMeta>
                      <span
                        className={`systems-badge systems-type-${system.system_type}`}
                        style={{ backgroundColor: typeInfo.bg, color: typeInfo.color }}
                      >
                        {typeInfo.label}
                      </span>
                    </RowMeta>

                    {/* Vendor */}
                    <RowMeta>
                      <span className="system-vendor-cell">
                        {system.vendor || '-'}
                        {(system.vendor_id || system.linked_vendor) && (
                          <span title="Linked to vendor record" style={{ marginLeft: '4px' }}>🔗</span>
                        )}
                      </span>
                    </RowMeta>

                    {/* Status badge */}
                    <RowMeta>
                      <span
                        className={`systems-badge systems-status-${system.status}`}
                        style={{ backgroundColor: statusInfo.bg, color: statusInfo.color }}
                      >
                        {statusInfo.label}
                      </span>
                    </RowMeta>

                    {/* Interfaces count */}
                    <RowMeta>
                      {compatibleInterfaces.length > 0 ? (
                        <span
                          title={compatibleInterfaces.map(ci => ci.interface.title).join(', ')}
                          className="systems-interfaces-badge"
                        >
                          {compatibleInterfaces.length} interface{compatibleInterfaces.length !== 1 ? 's' : ''}
                        </span>
                      ) : (
                        <span className="systems-no-interfaces">-</span>
                      )}
                    </RowMeta>

                    {/* Edit / Delete actions — outside row-click with stopPropagation */}
                    <RowMeta>
                      <div
                        className="system-row-actions"
                        onClick={(e) => e.stopPropagation()}
                      >
                        <button
                          onClick={(e) => {
                            e.stopPropagation()
                            onEditSystem(system)
                          }}
                          className="systems-btn systems-btn-edit"
                        >
                          Edit
                        </button>
                        {deleteConfirm === system.id ? (
                          <div className="systems-delete-confirm">
                            <button
                              onClick={(e) => {
                                e.stopPropagation()
                                handleDeleteSystem(system.id)
                              }}
                              className="systems-btn systems-btn-confirm-yes"
                            >
                              Yes
                            </button>
                            <button
                              onClick={(e) => {
                                e.stopPropagation()
                                setDeleteConfirm(null)
                              }}
                              className="systems-btn systems-btn-confirm-no"
                            >
                              No
                            </button>
                          </div>
                        ) : (
                          <button
                            onClick={(e) => {
                              e.stopPropagation()
                              setDeleteConfirm(system.id)
                            }}
                            className="systems-btn systems-btn-delete"
                          >
                            Delete
                          </button>
                        )}
                      </div>
                    </RowMeta>
                  </ExplorerListRow>
                )
              })
            )}
          </div>
        </div>
      </div>
    </div>
  )
}

export default SystemsRegistry
