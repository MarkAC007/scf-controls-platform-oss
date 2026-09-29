/**
 * DeleteOrganizationDialog — the confirmation in front of deleting a client
 * organisation and ALL of its data.
 *
 * Two deliberate frictions, matching the API contract (the backend refuses
 * the request without both): the person types the organisation's exact name,
 * and ticks that they understand everything is deleted. Neither is a
 * courtesy — the same body is required by DELETE /organizations/{id}, so
 * there is no cheaper door.
 */
import { useState } from 'react'
import { useModalDismiss } from '../../hooks/useModalDismiss'

interface DeleteOrganizationDialogProps {
  organizationName: string
  onConfirm: (confirmName: string) => Promise<void>
  onClose: () => void
}

export default function DeleteOrganizationDialog({
  organizationName,
  onConfirm,
  onClose,
}: DeleteOrganizationDialogProps) {
  const [typedName, setTypedName] = useState('')
  const [acknowledged, setAcknowledged] = useState(false)
  const [deleting, setDeleting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useModalDismiss(!deleting, onClose)

  const nameMatches = typedName === organizationName
  const canDelete = nameMatches && acknowledged && !deleting

  const handleConfirm = async () => {
    if (!canDelete) return
    setDeleting(true)
    setError(null)
    try {
      await onConfirm(typedName)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to delete organisation')
      setDeleting(false)
    }
  }

  return (
    <div className="modal-overlay" onClick={deleting ? undefined : onClose}>
      <div
        className="modal-content delete-org-dialog"
        onClick={e => e.stopPropagation()}
        role="dialog"
        aria-modal="true"
        aria-labelledby="delete-org-title"
      >
        <div className="modal-header">
          <h2 id="delete-org-title">Delete organisation</h2>
          <button className="modal-close" onClick={onClose} aria-label="Close" disabled={deleting}>
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <line x1="18" y1="6" x2="6" y2="18" />
              <line x1="6" y1="6" x2="18" y2="18" />
            </svg>
          </button>
        </div>
        <div className="modal-body">
          <p className="modal-description">
            You are about to permanently delete <strong>{organizationName}</strong> and{' '}
            <strong>all data related to it</strong>: scoped controls, evidence and every uploaded
            file, tasks, risks, vendors, audit engagements, generated documents, members, and the
            audit history. This cannot be undone.
          </p>

          <div className="form-group">
            <label htmlFor="delete-org-confirm-name">
              Type <code>{organizationName}</code> to confirm
            </label>
            <input
              id="delete-org-confirm-name"
              type="text"
              value={typedName}
              onChange={e => setTypedName(e.target.value)}
              placeholder={organizationName}
              autoComplete="off"
              spellCheck={false}
              disabled={deleting}
            />
          </div>

          <label className="delete-org-acknowledge">
            <input
              type="checkbox"
              checked={acknowledged}
              onChange={e => setAcknowledged(e.target.checked)}
              disabled={deleting}
            />
            <span>I understand that all data for this organisation will be permanently deleted.</span>
          </label>

          {error && (
            <div className="error-banner" role="alert">
              <span>{error}</span>
            </div>
          )}
        </div>
        <div className="modal-footer">
          <button className="btn-secondary" onClick={onClose} disabled={deleting}>
            Cancel
          </button>
          <button className="btn btn-danger" onClick={handleConfirm} disabled={!canDelete}>
            {deleting ? 'Deleting…' : 'Delete organisation and all its data'}
          </button>
        </div>
      </div>
    </div>
  )
}
