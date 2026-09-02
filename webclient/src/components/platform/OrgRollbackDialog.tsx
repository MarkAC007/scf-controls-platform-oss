/**
 * OrgRollbackDialog — typed confirmation before rolling back the latest
 * applied reconciliation run (POST .../rollback with {confirm_text}).
 *
 * Snapshot restore is the authority: pre-imaged rows are restored verbatim,
 * run-created rows are deleted only if unreferenced by an engagement, else
 * demoted (plan §4.3). The admin must type the exact version to confirm.
 */
import { useState } from 'react'

import { useModalDismiss } from '../../hooks/useModalDismiss'

interface OrgRollbackDialogProps {
  organizationName: string
  toVersion: string
  fromVersion?: string | null
  /** Rows the rollback restores — the run's snapshot row count. */
  rowCount: number
  rollingBack: boolean
  onConfirm: (confirmText: string) => void
  onClose: () => void
}

export default function OrgRollbackDialog({
  organizationName,
  toVersion,
  fromVersion,
  rowCount,
  rollingBack,
  onConfirm,
  onClose,
}: OrgRollbackDialogProps) {
  useModalDismiss(true, onClose)

  const [text, setText] = useState('')
  const matches = text.trim() === toVersion

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal-content" onClick={e => e.stopPropagation()}>
        <div className="modal-header">
          <h2>Roll back reconciliation</h2>
          <button className="modal-close" onClick={onClose} aria-label="Close">
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <line x1="18" y1="6" x2="6" y2="18" />
              <line x1="6" y1="6" x2="18" y2="18" />
            </svg>
          </button>
        </div>
        <div style={{ padding: '0 1.5rem 1.5rem' }}>
          <p>
            This restores <strong>{rowCount}</strong> row{rowCount === 1 ? '' : 's'} of{' '}
            <strong>{organizationName}</strong> to their pre-<strong>{toVersion}</strong> state
            from the run's snapshot
            {fromVersion && (
              <>
                {' '}and returns the organisation to catalog <strong>{fromVersion}</strong>
              </>
            )}
            . Rows referenced by an audit engagement are demoted rather than deleted.
          </p>
          <p style={{ color: 'var(--muted)', fontSize: '0.875rem' }}>
            Type <strong>{toVersion}</strong> to confirm.
          </p>
          <input
            type="text"
            aria-label="Confirm rollback version"
            placeholder={toVersion}
            value={text}
            onChange={e => setText(e.target.value)}
            autoFocus
            style={{ width: '100%', marginBottom: '1rem' }}
          />
          <div style={{ display: 'flex', gap: '0.75rem', justifyContent: 'flex-end' }}>
            <button className="btn btn-secondary" onClick={onClose} disabled={rollingBack}>
              Cancel
            </button>
            <button
              className="btn btn-danger"
              disabled={!matches || rollingBack}
              onClick={() => onConfirm(text.trim())}
            >
              {rollingBack ? 'Rolling back…' : 'Roll back'}
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}
