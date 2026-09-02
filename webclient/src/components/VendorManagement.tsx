import { useState, useCallback } from 'react'
import VendorRegistry from './VendorRegistry'
import VendorDetailPage from './VendorDetailPage'
import VendorModal from './VendorModal'
import { deleteVendor } from '../data/apiClient'
import { useModalDismiss } from '../hooks/useModalDismiss'
import type { Vendor } from '../types'

interface VendorManagementProps {
  organizationId: string
  /** The vendor id to show in detail, from ?vendor= URL param. null = list view. */
  vendorItem?: string | null
  /** Called when user opens/closes/pages detail. App owns push/replace decision. */
  onVendorItemChange?: (id: string | null) => void
}

/**
 * VendorManagement - orchestrates the vendor list, detail, and modal views.
 *
 * Phase 4 Task 5: URL-driven routing via vendorItem/onVendorItemChange (mirrors
 * RiskDashboard's riskItem/onRiskItemChange pattern). App owns URL writes.
 *
 * States:
 *   - vendorItem null: Shows VendorRegistry (list stays mounted beneath for filter/pager state)
 *   - vendorItem set: Shows VendorDetailPage full-width
 *   - Modal overlay: Shows VendorModal for creating/editing a vendor
 */
export default function VendorManagement({ organizationId, vendorItem = null, onVendorItemChange }: VendorManagementProps) {
  // URL-driven: no local view/selectedVendorId state — App owns them via vendorItem/onVendorItemChange
  const [filteredVendors, setFilteredVendors] = useState<Vendor[]>([])
  const [showModal, setShowModal] = useState(false)
  const [editVendor, setEditVendor] = useState<Vendor | null>(null)
  const [refreshKey, setRefreshKey] = useState(0)
  const [deleteConfirm, setDeleteConfirm] = useState<{ vendor: Vendor } | null>(null)
  const [deleting, setDeleting] = useState(false)

  // Same guard the backdrop uses — a delete in flight holds the dialog open.
  // VendorModal owns its own dismissal, and being the later registration it
  // takes Escape while it is the one on top.
  useModalDismiss(deleteConfirm !== null, () => {
    if (!deleting) setDeleteConfirm(null)
  })

  const handleSelectVendor = useCallback((vendorId: string) => {
    onVendorItemChange?.(vendorId)
  }, [onVendorItemChange])

  const handleAddVendor = useCallback(() => {
    setEditVendor(null)
    setShowModal(true)
  }, [])

  const handleEditVendor = useCallback((vendor: Vendor) => {
    setEditVendor(vendor)
    setShowModal(true)
  }, [])

  const handleDeleteVendor = useCallback((vendor: Vendor) => {
    setDeleteConfirm({ vendor })
  }, [])

  const handleConfirmDelete = useCallback(async () => {
    if (!deleteConfirm) return
    setDeleting(true)
    try {
      await deleteVendor(deleteConfirm.vendor.id, organizationId)
      setDeleteConfirm(null)
      onVendorItemChange?.(null)
      setRefreshKey(prev => prev + 1)
    } catch (err) {
      console.error('Failed to delete vendor:', err)
      alert(err instanceof Error ? err.message : 'Failed to delete vendor. Please try again.')
    } finally {
      setDeleting(false)
    }
  }, [deleteConfirm, organizationId, onVendorItemChange])

  const handleModalClose = useCallback(() => {
    setShowModal(false)
    setEditVendor(null)
  }, [])

  const handleModalSuccess = useCallback(() => {
    setShowModal(false)
    setEditVendor(null)
    setRefreshKey(prev => prev + 1)
  }, [])

  return (
    <>
      {/* List always mounted — stays alive for filter state and pager.
          display:contents (not block) when visible so the wrapper div adds no
          stacking context or box model of its own; display:none when the detail
          page is open makes the list non-focusable (keyboard+AT safe) while
          keeping it mounted so VendorRegistry's filteredVendors state survives. */}
      <div
        style={{ display: vendorItem ? 'none' : 'contents' }}
        aria-hidden={vendorItem ? true : undefined}
      >
        <VendorRegistry
          key={refreshKey}
          organizationId={organizationId}
          onSelectVendor={handleSelectVendor}
          onAddVendor={handleAddVendor}
          onDeleteVendor={handleDeleteVendor}
          onFilteredListChange={setFilteredVendors}
        />
      </div>

      {vendorItem && (
        <VendorDetailPage
          organizationId={organizationId}
          vendorId={vendorItem}
          filteredVendors={filteredVendors}
          onVendorItemChange={onVendorItemChange ?? (() => {})}
          onEdit={handleEditVendor}
          onDelete={handleDeleteVendor}
        />
      )}

      {showModal && (
        <VendorModal
          organizationId={organizationId}
          editVendor={editVendor}
          onClose={handleModalClose}
          onSuccess={handleModalSuccess}
        />
      )}

      {deleteConfirm && (
        <div
          className="modal-overlay"
          onClick={() => !deleting && setDeleteConfirm(null)}
          style={{
            position: 'fixed',
            top: 0,
            left: 0,
            right: 0,
            bottom: 0,
            background: 'rgba(0, 0, 0, 0.5)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            zIndex: 1000,
          }}
        >
          <div
            onClick={e => e.stopPropagation()}
            style={{
              background: 'var(--card)',
              borderRadius: '12px',
              padding: '24px',
              width: '100%',
              maxWidth: '440px',
              margin: '20px',
              border: '1px solid var(--border)',
            }}
          >
            <h3 style={{ margin: '0 0 12px 0', fontSize: '1.125rem', fontWeight: 600, color: 'var(--text)' }}>
              Delete Vendor
            </h3>
            <p style={{ margin: '0 0 8px 0', fontSize: '0.875rem', color: 'var(--text)' }}>
              Are you sure you want to delete <strong>{deleteConfirm.vendor.name}</strong>?
            </p>
            <p style={{ margin: '0 0 20px 0', fontSize: '0.8rem', color: 'var(--muted)' }}>
              This will permanently remove the vendor and all associated assessments, certifications, reports,
              and other related records. This action cannot be undone.
            </p>
            <div style={{ display: 'flex', justifyContent: 'flex-end', gap: '12px' }}>
              <button
                onClick={() => setDeleteConfirm(null)}
                disabled={deleting}
                style={{
                  padding: '8px 16px',
                  background: 'var(--secondary)',
                  border: '1px solid var(--border)',
                  borderRadius: '6px',
                  color: 'var(--text)',
                  cursor: deleting ? 'not-allowed' : 'pointer',
                  fontSize: '0.875rem',
                  opacity: deleting ? 0.6 : 1,
                }}
              >
                Cancel
              </button>
              <button
                onClick={handleConfirmDelete}
                disabled={deleting}
                style={{
                  padding: '8px 16px',
                  background: '#ef4444',
                  border: 'none',
                  borderRadius: '6px',
                  color: '#ffffff',
                  cursor: deleting ? 'not-allowed' : 'pointer',
                  fontSize: '0.875rem',
                  fontWeight: 500,
                  opacity: deleting ? 0.6 : 1,
                }}
              >
                {deleting ? 'Deleting...' : 'Delete Vendor'}
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  )
}
