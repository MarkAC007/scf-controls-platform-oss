/**
 * ApplyConfirmDialog — typed confirmation before applying a staged run.
 *
 * The admin must type the exact target version (e.g. "2026.2"); the backend
 * re-verifies both expected_to_version and confirm_text (plan §4.5).
 */
import { useState } from 'react'

interface ApplyConfirmDialogProps {
  toVersion: string
  applying: boolean
  onConfirm: (confirmText: string) => void
  onClose: () => void
}

export default function ApplyConfirmDialog({ toVersion, applying, onConfirm, onClose }: ApplyConfirmDialogProps) {
  const [text, setText] = useState('')
  const matches = text.trim() === toVersion

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal-content" onClick={e => e.stopPropagation()}>
        <div className="modal-header">
          <h2>Apply catalog upgrade</h2>
          <button className="modal-close" onClick={onClose} aria-label="Close">
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <line x1="18" y1="6" x2="6" y2="18" />
              <line x1="6" y1="6" x2="18" y2="18" />
            </svg>
          </button>
        </div>
        <div style={{ padding: '0 1.5rem 1.5rem' }}>
          <p>
            This upgrades the <strong>platform-wide</strong> catalog to{' '}
            <strong>{toVersion}</strong>. Every tenant organisation will see the new version
            as available for reconciliation.
          </p>
          <p style={{ color: 'var(--muted)', fontSize: '0.875rem' }}>
            Type <strong>{toVersion}</strong> to confirm.
          </p>
          <input
            type="text"
            aria-label="Confirm version"
            placeholder={toVersion}
            value={text}
            onChange={e => setText(e.target.value)}
            autoFocus
            style={{ width: '100%', marginBottom: '1rem' }}
          />
          <div style={{ display: 'flex', gap: '0.75rem', justifyContent: 'flex-end' }}>
            <button className="btn btn-secondary" onClick={onClose} disabled={applying}>
              Cancel
            </button>
            <button
              className="btn btn-primary"
              disabled={!matches || applying}
              onClick={() => onConfirm(text.trim())}
            >
              {applying ? 'Applying…' : `Apply ${toVersion}`}
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}
