/**
 * SystemsManagement — orchestrates system list, detail, and modal views.
 *
 * Pattern: hidden-list (mirrors VendorManagement idiom).
 * The list (SystemsRegistry) stays mounted beneath the detail page for filter
 * state preservation and pager. onFilteredListChange keeps filteredSystems in
 * sync so the pager "k of N" is always the current filtered count.
 *
 * States:
 *   - systemItem null: list visible, detail hidden
 *   - systemItem set: detail visible, list hidden (but mounted)
 *   - Modal overlay: AddSystemModal for create/edit
 *
 * Chosen over the early-return (RiskDashboard) pattern because:
 *   - SystemsRegistry has its own filter state (type, status, search) that
 *     must survive round-trips to the detail page.
 *   - The pager walks the current FILTERED list, not the full list — so the
 *     registry must stay mounted to provide that state.
 */
import { useState, useCallback } from 'react'
import SystemsRegistry from './SystemsRegistry'
import SystemDetailPage from './SystemDetailPage'
import AddSystemModal from './AddSystemModal'
import type { System, CollectionInterfacesFile } from '../types'

interface SystemsManagementProps {
  organizationId: string
  collectionInterfaces?: CollectionInterfacesFile
  /** The system id to show in detail, from ?system= URL param. null = list view. */
  systemItem?: string | null
  /** Called when user opens/closes/pages the detail. App owns push/replace decision. */
  onSystemItemChange?: (id: string | null) => void
}

export default function SystemsManagement({
  organizationId,
  collectionInterfaces,
  systemItem = null,
  onSystemItemChange,
}: SystemsManagementProps) {
  const [filteredSystems, setFilteredSystems] = useState<System[]>([])
  const [showModal, setShowModal] = useState(false)
  const [editingSystem, setEditingSystem] = useState<System | null>(null)
  const [refreshKey, setRefreshKey] = useState(0)

  const handleSelectSystem = useCallback((system: System) => {
    onSystemItemChange?.(system.id)
  }, [onSystemItemChange])

  const handleAddSystem = useCallback(() => {
    setEditingSystem(null)
    setShowModal(true)
  }, [])

  const handleEditSystem = useCallback((system: System) => {
    setEditingSystem(system)
    setShowModal(true)
  }, [])

  const handleModalClose = useCallback(() => {
    setShowModal(false)
    setEditingSystem(null)
  }, [])

  const handleModalSuccess = useCallback(() => {
    setShowModal(false)
    setEditingSystem(null)
    setRefreshKey(prev => prev + 1)
  }, [])

  return (
    <>
      {/* List always mounted — hidden when detail is active */}
      <div style={{ display: systemItem ? 'none' : 'contents' }}>
        <SystemsRegistry
          key={refreshKey}
          organizationId={organizationId}
          collectionInterfaces={collectionInterfaces}
          onAddSystem={handleAddSystem}
          onEditSystem={handleEditSystem}
          onViewSystem={handleSelectSystem}
          onFilteredListChange={setFilteredSystems}
        />
      </div>

      {/* Detail page */}
      {systemItem && (
        <SystemDetailPage
          organizationId={organizationId}
          systemId={systemItem}
          filteredSystems={filteredSystems}
          onSystemItemChange={onSystemItemChange ?? (() => {})}
          onEdit={(sys) => {
            setEditingSystem(sys)
            setShowModal(true)
          }}
        />
      )}

      {/* Modal: create + edit */}
      {showModal && (
        <AddSystemModal
          organizationId={organizationId}
          editSystem={editingSystem}
          onClose={handleModalClose}
          onSuccess={handleModalSuccess}
        />
      )}
    </>
  )
}
