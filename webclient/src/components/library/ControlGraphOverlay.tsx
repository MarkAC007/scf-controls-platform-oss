/**
 * ControlGraphOverlay — the control's relationship graph over the whole
 * browser window.
 *
 * Portalled to <body> so no ancestor's overflow or stacking clips it. Escape
 * closes it — handled in the capture phase so the detail page's own Escape
 * ("back to Controls") never sees it — unless focus is in a field with text,
 * where Escape clears the field first. Focus returns to the opener on close.
 */
import { useEffect, useRef, type JSX } from 'react'
import { createPortal } from 'react-dom'
import GraphView from '../GraphView'
import type { EnrichedControl } from '../../types'

interface Props {
  control: EnrichedControl
  onClose: () => void
  onOpenEvidence?: (evidenceId: string) => void
}

export default function ControlGraphOverlay({ control, onClose, onOpenEvidence }: Props): JSX.Element {
  const closeRef = useRef<HTMLButtonElement>(null)
  // Latest onClose without re-running the mount effect (which would move focus).
  const onCloseRef = useRef(onClose)
  onCloseRef.current = onClose

  useEffect(() => {
    const opener = document.activeElement as HTMLElement | null
    closeRef.current?.focus()
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key !== 'Escape') return
      const t = e.target as HTMLInputElement | null
      if (t && t.tagName === 'INPUT' && t.value) return // the field clears itself first
      e.preventDefault()
      e.stopImmediatePropagation()
      onCloseRef.current()
    }
    window.addEventListener('keydown', onKeyDown, true)
    const { overflow } = document.body.style
    document.body.style.overflow = 'hidden'
    return () => {
      window.removeEventListener('keydown', onKeyDown, true)
      document.body.style.overflow = overflow
      if (opener && opener !== document.body) opener.focus()
    }
  }, [])

  return createPortal(
    <div
      className="control-graph-overlay"
      role="dialog"
      aria-modal="true"
      aria-label={`${control.scf_id} relationship graph`}
    >
      <header className="control-graph-overlay-header">
        <span className="control-graph-overlay-id">{control.scf_id}</span>
        <span className="control-graph-overlay-title">{control.control_name}</span>
        <span className="control-graph-overlay-sub">Relationship graph</span>
        <button
          ref={closeRef}
          type="button"
          className="control-graph-overlay-close"
          onClick={onClose}
          aria-label="Close graph"
          title="Close (Esc)"
        >
          <svg width="14" height="14" viewBox="0 0 14 14" fill="none" aria-hidden="true">
            <path d="M3 3l8 8M11 3l-8 8" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
          </svg>
          <span>Close</span>
        </button>
      </header>
      <div className="control-graph-overlay-body">
        <GraphView
          control={control}
          onOpenEvidence={
            onOpenEvidence
              ? (id) => {
                  onClose()
                  onOpenEvidence(id)
                }
              : undefined
          }
        />
      </div>
    </div>,
    document.body,
  )
}
