import React, { useState, useEffect, useMemo } from 'react'
import { getSystems, deleteSystem } from '../data/apiClient'
import type { System, SystemType, SystemStatus, CollectionInterfacesFile, CollectionInterface } from '../types'

interface SystemsRegistryProps {
  organizationId?: string
  collectionInterfaces?: CollectionInterfacesFile
  onAddSystem: () => void
  onEditSystem: (system: System) => void
  onViewSystem: (system: System) => void
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
  custom: { label: 'Custom', color: '#666', bg: '#f5f5f5' },
}

// Status display configuration
const statusConfig: Record<SystemStatus, { label: string; color: string; bg: string }> = {
  active: { label: 'Active', color: '#388e3c', bg: '#e8f5e9' },
  inactive: { label: 'Inactive', color: '#f57c00', bg: '#fff3e0' },
  deprecated: { label: 'Deprecated', color: '#d32f2f', bg: '#ffebee' },
}

export const SystemsRegistry: React.FC<SystemsRegistryProps> = ({
  organizationId,
  collectionInterfaces,
  onAddSystem,
  onEditSystem,
  onViewSystem,
}) => {
  const [systems, setSystems] = useState<System[]>([])
  const [loading, setLoading] = useState(true)
  const [typeFilter, setTypeFilter] = useState<string>('all')
  const [statusFilter, setStatusFilter] = useState<string>('all')
  const [searchQuery, setSearchQuery] = useState('')
  const [deleteConfirm, setDeleteConfirm] = useState<string | null>(null)

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
  const filteredSystems = systems.filter(system => {
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
  })

  // Group systems by type for stats
  const stats = {
    total: systems.length,
    active: systems.filter(s => s.status === 'active').length,
    byType: Object.entries(
      systems.reduce((acc, s) => {
        acc[s.system_type] = (acc[s.system_type] || 0) + 1
        return acc
      }, {} as Record<string, number>)
    ),
  }

  return (
    <div className="systems-page">
      {/* Header */}
      <div className="systems-header">
        <div>
          <h1>Systems Registry</h1>
          <p className="systems-subtitle">
            Manage systems that collect evidence for compliance controls
          </p>
        </div>
        <button onClick={onAddSystem} className="systems-add-btn">
          <span style={{ fontSize: '1.25rem' }}>+</span> Add System
        </button>
      </div>

      {/* Stats */}
      <div className="systems-stats-grid">
        <div className="systems-stat-card">
          <div className="systems-stat-value text-blue">{stats.total}</div>
          <div className="systems-stat-label">Total Systems</div>
        </div>
        <div className="systems-stat-card">
          <div className="systems-stat-value text-green">{stats.active}</div>
          <div className="systems-stat-label">Active</div>
        </div>
        {stats.byType.slice(0, 3).map(([type, count]) => (
          <div key={type} className="systems-stat-card">
            <div className="systems-stat-value" style={{ color: systemTypeConfig[type as SystemType]?.color || '#666' }}>
              {count}
            </div>
            <div className="systems-stat-label">
              {systemTypeConfig[type as SystemType]?.label || type}
            </div>
          </div>
        ))}
      </div>

      {/* Filters */}
      <div className="systems-filters">
        <div className="systems-search-wrapper">
          <input
            type="text"
            placeholder="Search systems..."
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            className="systems-search-input"
          />
        </div>

        <div className="systems-filter-group">
          <label>Type:</label>
          <select
            value={typeFilter}
            onChange={(e) => setTypeFilter(e.target.value)}
            className="systems-filter-select"
          >
            <option value="all">All Types</option>
            {Object.entries(systemTypeConfig).map(([key, config]) => (
              <option key={key} value={key}>{config.label}</option>
            ))}
          </select>
        </div>

        <div className="systems-filter-group">
          <label>Status:</label>
          <select
            value={statusFilter}
            onChange={(e) => setStatusFilter(e.target.value)}
            className="systems-filter-select"
          >
            <option value="all">All Statuses</option>
            {Object.entries(statusConfig).map(([key, config]) => (
              <option key={key} value={key}>{config.label}</option>
            ))}
          </select>
        </div>
      </div>

      {/* Systems List */}
      {loading ? (
        <div className="systems-loading">Loading systems...</div>
      ) : filteredSystems.length === 0 ? (
        <div className="systems-empty-state">
          <div className="systems-empty-icon">🖥️</div>
          <h3>No Systems Found</h3>
          <p>
            {systems.length === 0
              ? 'Add your first system to start tracking evidence collection capabilities.'
              : 'Try adjusting the filters to see more results.'}
          </p>
          {systems.length === 0 && (
            <button
              onClick={onAddSystem}
              style={{
                marginTop: '1rem',
                padding: '0.75rem 1.5rem',
                backgroundColor: '#1976d2',
                color: 'white',
                border: 'none',
                borderRadius: '4px',
                cursor: 'pointer',
                fontWeight: 600,
              }}
            >
              Add Your First System
            </button>
          )}
        </div>
      ) : (
        <div className="systems-list-container">
          {/* Table Header */}
          <div className="systems-table-header">
            <div>System</div>
            <div>Type</div>
            <div>Vendor</div>
            <div>Status</div>
            <div>Interfaces</div>
            <div>Actions</div>
          </div>

          {/* System Rows */}
          {filteredSystems.map((system) => {
            const typeInfo = systemTypeConfig[system.system_type] || systemTypeConfig.custom
            const statusInfo = statusConfig[system.status] || statusConfig.active
            const compatibleInterfaces = getCompatibleInterfaces(system.system_type, collectionInterfaces)

            return (
              <div key={system.id} className="systems-table-row">
                {/* System Name & Description */}
                <div>
                  <div
                    className="systems-name-link"
                    onClick={() => onViewSystem(system)}
                  >
                    {system.name}
                  </div>
                  {system.description && (
                    <div className="systems-description">
                      {system.description.length > 60
                        ? `${system.description.substring(0, 60)}...`
                        : system.description}
                    </div>
                  )}
                  {system.category && (
                    <div className="systems-category">
                      Category: {system.category}
                    </div>
                  )}
                </div>

                {/* Type Badge */}
                <div>
                  <span
                    className={`systems-badge systems-type-${system.system_type}`}
                    style={{ backgroundColor: typeInfo.bg, color: typeInfo.color }}
                  >
                    {typeInfo.label}
                  </span>
                </div>

                {/* Vendor */}
                <div className="systems-vendor">
                  {system.vendor || '-'}
                </div>

                {/* Status Badge */}
                <div>
                  <span
                    className={`systems-badge systems-status-${system.status}`}
                    style={{ backgroundColor: statusInfo.bg, color: statusInfo.color }}
                  >
                    {statusInfo.label}
                  </span>
                </div>

                {/* Compatible Interfaces */}
                <div>
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
                </div>

                {/* Actions */}
                <div className="systems-actions">
                  <button
                    onClick={() => onEditSystem(system)}
                    className="systems-btn systems-btn-edit"
                  >
                    Edit
                  </button>
                  {deleteConfirm === system.id ? (
                    <div className="systems-delete-confirm">
                      <button
                        onClick={() => handleDeleteSystem(system.id)}
                        className="systems-btn systems-btn-confirm-yes"
                      >
                        Yes
                      </button>
                      <button
                        onClick={() => setDeleteConfirm(null)}
                        className="systems-btn systems-btn-confirm-no"
                      >
                        No
                      </button>
                    </div>
                  ) : (
                    <button
                      onClick={() => setDeleteConfirm(system.id)}
                      className="systems-btn systems-btn-delete"
                    >
                      Delete
                    </button>
                  )}
                </div>
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}

export default SystemsRegistry
