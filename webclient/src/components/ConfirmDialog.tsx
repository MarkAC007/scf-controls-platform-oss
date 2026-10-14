/**
 * ConfirmDialog — the in-app confirmation step (#1157).
 *
 * Replaces the native confirm() and prompt() dialogs. Those could not be themed,
 * showed the consequence as one unstyled line, and blocked the renderer, which
 * froze browser automation during the PIG UI QA.
 *
 * Presentational only: open it through `useConfirm()` from
 * `contexts/ConfirmContext`, which mounts this while a request is pending and
 * turns the answer into a promise. Mounted only while open, so the focus hooks
 * capture the trigger on mount and hand focus back to it on unmount.
 *
 * - `role="alertdialog"`, labelled by the title and described by the
 *   consequence paragraph, so a screen reader announces the message verbatim.
 * - Escape, the backdrop and Cancel all cancel (`useModalDismiss`, which also
 *   stacks correctly when this opens over another modal). The backdrop
 *   cancels only for a press that starts and ends on it, so a text selection
 *   or a drag out of the reason field does not throw the answer away.
 * - Tab is trapped inside (`useDialogFocus`). A destructive confirmation opens
 *   on Cancel, so Enter on arrival never runs the destructive action.
 * - A key held down when the dialog opens is not an answer. Enter activates a
 *   button on keydown, so a held Enter on the trigger auto-repeats onto the
 *   focused Confirm; repeats are ignored until a key is released.
 * - Keys are not swallowed here. Page shortcuts behind the dialog stand down
 *   on their own (`keyEventBelongsToModal`).
 * - With `reason`, a text field is shown (this is what replaced
 *   the native prompt). Confirm is disabled until the field is acceptable.
 */
import { useEffect, useId, useRef, useState, type KeyboardEvent, type MouseEvent } from 'react'

import { useDialogFocus } from '../hooks/useDialogFocus'
import { useModalDismiss } from '../hooks/useModalDismiss'

/** A text field the user fills in before confirming. */
export interface ConfirmReasonField {
  /** Visible label of the field; also its accessible name. */
  label: string
  placeholder?: string
  /**
   * Whether an empty (whitespace-only) value blocks confirm. Default `true`.
   * Ignored when `mustEqual` is set.
   */
  required?: boolean
  /**
   * Typed confirmation: confirm is enabled only when the field equals this
   * exactly — no trimming, no case folding, as the native prompt comparison did.
   */
  mustEqual?: string
}

export interface ConfirmOptions {
  /** Short heading; the dialog's accessible name. */
  title: string
  /**
   * The consequence text, rendered verbatim (line breaks kept) and referenced
   * by `aria-describedby`. Pass the reviewed wording unchanged.
   */
  message: string
  /** Label of the confirming button. Default "Confirm". */
  confirmLabel?: string
  /** Label of the cancelling button. Default "Cancel". */
  cancelLabel?: string
  /** Use the danger button and open with focus on Cancel. */
  destructive?: boolean
}

export interface ConfirmDialogProps extends ConfirmOptions {
  reason?: ConfirmReasonField
  /** Called with the accepted reason value when `reason` is set, else with nothing. */
  onConfirm: (reason?: string) => void
  onCancel: () => void
}

/** The value a reason field resolves with, or `null` while it is not acceptable. */
export function acceptedReason(field: ConfirmReasonField, text: string): string | null {
  if (field.mustEqual !== undefined) return text === field.mustEqual ? text : null
  const value = text.trim()
  if (value === '' && field.required !== false) return null
  return value
}

export default function ConfirmDialog({
  title,
  message,
  confirmLabel = 'Confirm',
  cancelLabel = 'Cancel',
  destructive = false,
  reason,
  onConfirm,
  onCancel,
}: ConfirmDialogProps) {
  const dialogRef = useRef<HTMLDivElement>(null)
  const titleId = useId()
  const messageId = useId()
  const reasonId = useId()
  const [text, setText] = useState('')

  useModalDismiss(true, onCancel)
  useDialogFocus(dialogRef)

  const accepted = reason ? acceptedReason(reason, text) : null
  const ready = !reason || accepted !== null

  const submit = () => {
    if (!ready) return
    if (reason) onConfirm(accepted as string)
    else onConfirm()
  }

  // Armed by the first key release after opening. Until then an auto-repeated
  // Enter or Space is the trigger's key still held down, not an answer.
  const armed = useRef(false)
  useEffect(() => {
    const arm = () => {
      armed.current = true
    }
    document.addEventListener('keyup', arm, true)
    return () => document.removeEventListener('keyup', arm, true)
  }, [])
  const ignoreHeldKey = (event: KeyboardEvent) => {
    if (armed.current || !event.repeat) return
    if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault()
      event.stopPropagation()
    }
  }

  // A click lands on the common ancestor of press and release, so a press in
  // the dialog released on the backdrop arrives as a backdrop click.
  const pressedOnBackdrop = useRef(false)
  const onBackdropMouseDown = (event: MouseEvent) => {
    pressedOnBackdrop.current = event.target === event.currentTarget
  }
  const onBackdropClick = (event: MouseEvent) => {
    const fromBackdrop = pressedOnBackdrop.current && event.target === event.currentTarget
    pressedOnBackdrop.current = false
    if (fromBackdrop) onCancel()
  }

  return (
    <div
      className="modal-overlay confirm-dialog-overlay"
      onMouseDown={onBackdropMouseDown}
      onClick={onBackdropClick}
    >
      <div
        ref={dialogRef}
        className="modal-content confirm-dialog"
        role="alertdialog"
        aria-modal="true"
        aria-labelledby={titleId}
        aria-describedby={messageId}
        onKeyDownCapture={ignoreHeldKey}
      >
        <h2 id={titleId} className="confirm-dialog-title">
          {title}
        </h2>
        <p id={messageId} className="confirm-dialog-message">
          {message}
        </p>
        {reason && (
          <div className="confirm-dialog-reason">
            <label htmlFor={reasonId}>{reason.label}</label>
            <input
              id={reasonId}
              type="text"
              autoComplete="off"
              data-autofocus
              placeholder={reason.placeholder}
              value={text}
              onChange={(event) => setText(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === 'Enter') {
                  event.preventDefault()
                  submit()
                }
              }}
            />
          </div>
        )}
        <div className="confirm-dialog-actions">
          <button
            type="button"
            className="btn btn-secondary"
            onClick={onCancel}
            data-autofocus={!reason && destructive ? true : undefined}
          >
            {cancelLabel}
          </button>
          <button
            type="button"
            className={destructive ? 'btn btn-danger' : 'btn btn-primary'}
            disabled={!ready}
            onClick={submit}
            data-autofocus={!reason && !destructive ? true : undefined}
          >
            {confirmLabel}
          </button>
        </div>
      </div>
    </div>
  )
}
