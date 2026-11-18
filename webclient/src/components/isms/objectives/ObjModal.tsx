// SPDX-License-Identifier: AGPL-3.0-only
/**
 * The shell the objective modals share: overlay, dialog, header, form, footer.
 * House rules as DeficiencyFormModal: it cannot be dismissed while a save is
 * in flight, and Escape or a backdrop click is ignored once anything is typed
 * (Cancel and ✕ still close it).
 */
import { useId, useRef, type JSX, type ReactNode } from 'react'
import { useDialogFocus } from '../../../hooks/useDialogFocus'
import { useModalDismiss } from '../../../hooks/useModalDismiss'

interface Props {
  title: string
  /** Shown after the title, e.g. the objective's reference. */
  reference?: string
  /** Shown at the right of the header. */
  aside?: string
  small?: boolean
  busy: boolean
  dirty: boolean
  canSubmit: boolean
  submitLabel: string
  onSubmit: () => void
  onClose: () => void
  /** Left of the footer buttons. */
  footerNote?: ReactNode
  /** Between the note and the buttons, e.g. "Confirm now". */
  footerExtra?: ReactNode
  bodyClassName?: string
  children: ReactNode
}

export default function ObjModal({
  title, reference, aside, small, busy, dirty, canSubmit, submitLabel, onSubmit, onClose,
  footerNote, footerExtra, bodyClassName, children,
}: Props): JSX.Element {
  const dialogRef = useRef<HTMLDivElement>(null)
  useDialogFocus(dialogRef)
  const id = useId()
  const dismiss = () => {
    if (!busy) onClose()
  }
  const dismissIfClean = () => {
    if (!dirty) dismiss()
  }
  useModalDismiss(true, dismissIfClean)

  return (
    <div className="modal-overlay" onClick={dismissIfClean}>
      <div
        ref={dialogRef}
        className={`modal-content obj-modal${small ? ' obj-modal--small' : ''}`}
        role="dialog"
        aria-modal="true"
        aria-labelledby={`${id}-heading`}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="modal-header">
          <h2 id={`${id}-heading`}>{title}</h2>
          {reference && <span className="obj-ref">{reference}</span>}
          {aside && <span className="obj-modal-clause">{aside}</span>}
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
            if (canSubmit && !busy) onSubmit()
          }}
        >
          <div className={`modal-body${bodyClassName ? ` ${bodyClassName}` : ''}`}>{children}</div>
          <div className="modal-footer">
            {footerNote && <p className="obj-footer-note">{footerNote}</p>}
            {footerExtra}
            <button type="button" className="btn-secondary" onClick={dismiss} disabled={busy}>
              Cancel
            </button>
            <button type="submit" className="btn-primary" disabled={!canSubmit || busy}>
              {busy ? 'Saving…' : submitLabel}
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}
