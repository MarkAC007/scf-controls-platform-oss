/**
 * RevertDialog — platform revert of an applied run (POST .../revert).
 *
 * A 409 means organisations have already reconciled forward to this version;
 * the blocking orgs from the error payload are listed so the admin knows who
 * must roll back first (plan §4.6).
 */
import { useState } from 'react'
import { RevertBlockedError } from '../../data/catalogUpgradeApi'
import { useModalDismiss } from '../../hooks/useModalDismiss'

interface RevertDialogProps {
  toVersion: string
  onConfirm: () => Promise<void>
  onClose: () => void
}

export default function RevertDialog({ toVersion, onConfirm, onClose }: RevertDialogProps) {
  useModalDismiss(true, onClose)

  const [reverting, setReverting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [blockers, setBlockers] = useState<string[]>([])

  const handleConfirm = async () => {
    setReverting(true)
    setError(null)
    setBlockers([])
    try {
      await onConfirm()
    } catch (err: unknown) {
      if (err instanceof RevertBlockedError) {
        setError(err.message)
        setBlockers(err.blockers)
      } else {
        setError(err instanceof Error ? err.message : 'Failed to revert the upgrade')
      }
    } finally {
      setReverting(false)
    }
  }

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal-content" onClick={e => e.stopPropagation()}>
        <div className="modal-header">
          <h2>Revert catalog upgrade</h2>
          <button className="modal-close" onClick={onClose} aria-label="Close">
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <line x1="18" y1="6" x2="6" y2="18" />
              <line x1="6" y1="6" x2="18" y2="18" />
            </svg>
          </button>
        </div>
        <div style={{ padding: '0 1.5rem 1.5rem' }}>
          <p>
            This restores the platform catalog to its pre-<strong>{toVersion}</strong> state
            using the stored diff. A revert is only possible while no organisation has
            reconciled to {toVersion}.
          </p>
          {error && (
            <div
              role="alert"
              style={{
                border: '1px solid var(--danger, #ef4444)',
                borderRadius: '6px',
                padding: '0.75rem 1rem',
                marginBottom: '1rem',
              }}
            >
              <strong>Revert blocked.</strong> {error}
              {blockers.length > 0 && (
                <>
                  <p style={{ margin: '0.5rem 0 0.25rem' }}>
                    These organisations are reconciled to {toVersion} and must roll back first:
                  </p>
                  <ul style={{ margin: 0, paddingLeft: '1.25rem' }}>
                    {blockers.map(blocker => (
                      <li key={blocker}>{blocker}</li>
                    ))}
                  </ul>
                </>
              )}
            </div>
          )}
          <div style={{ display: 'flex', gap: '0.75rem', justifyContent: 'flex-end' }}>
            <button className="btn btn-secondary" onClick={onClose} disabled={reverting}>
              Close
            </button>
            <button className="btn btn-danger" disabled={reverting} onClick={handleConfirm}>
              {reverting ? 'Reverting…' : 'Revert upgrade'}
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}
