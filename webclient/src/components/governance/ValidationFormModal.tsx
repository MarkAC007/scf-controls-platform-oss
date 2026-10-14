/**
 * ValidationFormModal — record that a control has been validated (PIG R10).
 *
 * The note is the record: what was checked and what was found. The server
 * stamps the date and the person, and the control's cadence then gives the
 * next date. A validation replaces the control's last one; only the audit
 * log keeps the earlier, so nothing here undoes it.
 *
 * Mounted only while open. It cannot be dismissed while a save is in flight,
 * and Escape or a backdrop click is ignored once anything is typed; Cancel or
 * ✕ still close it. The server checks the role, and a refusal shows in the alert.
 */
import { useId, useRef, useState, type JSX } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { toast } from 'react-hot-toast'
import { recordControlValidation } from '../../data/apiClient'
import { useDialogFocus } from '../../hooks/useDialogFocus'
import { invalidateControlValidations } from '../../hooks/useDueCare'
import { useModalDismiss } from '../../hooks/useModalDismiss'
import { formatServerDay } from '../../utils/auditValue'

interface Props {
  organizationId: string
  scfId: string
  /** The validation this one replaces, if any. */
  lastValidatedAt?: string | null
  onClose: () => void
}

const errorText = (err: unknown) => (err instanceof Error ? err.message : String(err))

export default function ValidationFormModal({ organizationId, scfId, lastValidatedAt, onClose }: Props): JSX.Element {
  const queryClient = useQueryClient()
  const dialogRef = useRef<HTMLDivElement>(null)
  useDialogFocus(dialogRef)
  const id = useId()

  const [note, setNote] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const canSubmit = note.trim() !== '' && !busy
  const dirty = note !== ''

  const dismiss = () => {
    if (!busy) onClose()
  }
  const dismissIfClean = () => {
    if (!dirty) dismiss()
  }
  // Stays registered while busy or dirty, so Escape cannot reach the page behind.
  useModalDismiss(true, dismissIfClean)

  const submit = async () => {
    setBusy(true)
    setError(null)
    try {
      await recordControlValidation(organizationId, scfId, note.trim())
      toast.success('Validation recorded')
      await invalidateControlValidations(queryClient, organizationId)
      onClose()
    } catch (err) {
      setError(errorText(err))
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
          <h2 id={`${id}-heading`}>Record validation</h2>
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
            if (canSubmit) void submit()
          }}
        >
          <div className="modal-body">
            <div className="form-group">
              <span className="deficiency-form-label">Control</span>
              <p className="deficiency-subject">
                <span className="deficiency-subject-id">{scfId}</span>
              </p>
            </div>
            <div className="form-group">
              <label htmlFor={`${id}-note`}>What was checked and what was found</label>
              <textarea
                id={`${id}-note`}
                className="form-control"
                rows={4}
                value={note}
                placeholder="For example: sampled ten joiners against HR records; all had approved access requests."
                onChange={(e) => setNote(e.target.value)}
                readOnly={busy}
                aria-describedby={`${id}-note-hint`}
                aria-required="true"
                data-autofocus
              />
              <p id={`${id}-note-hint`} className="deficiency-field-hint">
                The record a reviewer reads later. It is dated today and in your name, and the control's cadence then
                gives the next date.
                {lastValidatedAt
                  ? ` It replaces the last validation (${formatServerDay(lastValidatedAt)}) shown on this control; the audit log keeps the earlier one.`
                  : ''}
              </p>
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
            <button type="submit" className="btn-primary" disabled={!canSubmit}>
              {busy ? 'Saving…' : 'Record'}
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}
