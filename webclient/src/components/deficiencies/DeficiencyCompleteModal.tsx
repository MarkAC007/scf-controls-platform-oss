/**
 * DeficiencyCompleteModal — close a deficiency as remediated (PIG R8).
 *
 * Completing needs a retest note: what was retested, and the result. The
 * server refuses a completion without one, and the database enforces it too.
 * Mounted per deficiency, so a note written for one is never sent for another.
 * Not dismissable while the write is in flight, and Escape or a backdrop
 * click is ignored once a note is typed; Cancel or ✕ still close it.
 */
import { useId, useRef, useState, type JSX } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { toast } from 'react-hot-toast'
import { updateDeficiency } from '../../data/apiClient'
import { invalidateDeficiencies } from '../../hooks/useDeficiencies'
import { useDialogFocus } from '../../hooks/useDialogFocus'
import { useModalDismiss } from '../../hooks/useModalDismiss'
import type { Deficiency } from '../../types'

interface Props {
  organizationId: string
  deficiency: Deficiency
  onClose: () => void
}

export default function DeficiencyCompleteModal({ organizationId, deficiency, onClose }: Props): JSX.Element {
  const queryClient = useQueryClient()
  const dialogRef = useRef<HTMLDivElement>(null)
  useDialogFocus(dialogRef)
  const id = useId()
  const [note, setNote] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const dismiss = () => {
    if (!busy) onClose()
  }
  const dismissIfClean = () => {
    if (!note.trim()) dismiss()
  }
  // Stays registered while busy or typed in, so Escape cannot reach the page behind.
  useModalDismiss(true, dismissIfClean)

  const submit = async () => {
    setBusy(true)
    setError(null)
    try {
      await updateDeficiency(organizationId, deficiency.id, { status: 'completed', retest_note: note.trim() })
      await invalidateDeficiencies(queryClient, organizationId)
      toast.success('Deficiency completed')
      onClose()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not complete the deficiency')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="modal-overlay" onClick={dismissIfClean}>
      <div
        ref={dialogRef}
        className="modal-content deficiency-modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby={`${id}-heading`}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="modal-header">
          <h2 id={`${id}-heading`}>Complete deficiency</h2>
          <button type="button" className="modal-close" onClick={dismiss} disabled={busy} aria-label="Close">
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
              <line x1="18" y1="6" x2="6" y2="18" />
              <line x1="6" y1="6" x2="18" y2="18" />
            </svg>
          </button>
        </div>
        <form
          onSubmit={(e) => {
            e.preventDefault()
            if (note.trim() && !busy) void submit()
          }}
        >
          <div className="modal-body">
            <p className="modal-description">
              <strong>{deficiency.title}</strong>
              {deficiency.scf_id && <> · {deficiency.scf_id}</>}
              {deficiency.risk_code && <> · {deficiency.risk_code}</>}
            </p>
            <div className="form-group">
              <label htmlFor={`${id}-note`}>Retest note</label>
              <textarea
                id={`${id}-note`}
                className="form-control"
                rows={4}
                value={note}
                placeholder="What was retested, and the result"
                onChange={(e) => setNote(e.target.value)}
                data-autofocus
              />
            </div>
            {error && (
              <div role="alert" className="gov-error">
                {error}
              </div>
            )}
          </div>
          <div className="modal-footer">
            <button type="button" className="btn-secondary" onClick={onClose} disabled={busy}>
              Cancel
            </button>
            <button type="submit" className="btn-primary" disabled={!note.trim() || busy}>
              {busy ? 'Saving…' : 'Complete'}
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}
