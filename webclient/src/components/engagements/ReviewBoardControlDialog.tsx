// SPDX-License-Identifier: AGPL-3.0-only
import { useRef } from 'react'
import type { NoEvidenceControl, ReviewViewer } from '../../data/engagementReviewApi'
import { useFocusTrap } from '../../hooks/useFocusTrap'
import { useModalDismiss } from '../../hooks/useModalDismiss'
import ReviewQueries from './ReviewQueries'

/**
 * A control with no evidence object never gets a card; it is resolved by a
 * control-level query. Mounted only while open.
 */
interface ReviewBoardControlDialogProps {
  organizationId: string
  engagementId: string
  frameworkLabel: string
  control: NoEvidenceControl
  viewer: ReviewViewer
  onClose: () => void
  onChanged: () => void
  onNavigateToControl?: (scfId: string, mode?: 'in-scope' | 'full-library') => void
}

export default function ReviewBoardControlDialog({
  organizationId, engagementId, frameworkLabel, control, viewer, onClose, onChanged, onNavigateToControl,
}: ReviewBoardControlDialogProps) {
  const ref = useRef<HTMLDivElement>(null)
  useModalDismiss(true, onClose)
  useFocusTrap(ref, true)
  const titleId = `erb-ctl-title-${control.scf_id}`

  return (
    <div className="erb-backdrop" onMouseDown={e => { if (e.target === e.currentTarget) onClose() }}>
      <div
        ref={ref}
        className="erb-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        tabIndex={-1}
      >
        <div className="erb-dialog-head">
          <div className="erb-dialog-heading">
            <h2 id={titleId} className="erb-dialog-title">
              <span className="erb-dialog-id">{control.scf_id}</span>
              {control.title && <span> {control.title}</span>}
            </h2>
            <p className="erb-dialog-sub">
              No evidence object maps to this control, so it stays Open until a control-level query resolves it.
            </p>
            <p className="erb-dialog-sub">
              It counts as Accepted once at least one query here is Closed and none is Open or Answered.
              Raising or reopening a query sets it back to Open.
            </p>
          </div>
          <button type="button" className="erb-icon-btn" aria-label="Close" onClick={onClose}>×</button>
        </div>
        {control.clauses.length > 0 && (
          <div className="erb-chip-row">
            <span className="erb-chip-label">{frameworkLabel}</span>
            {control.clauses.map(cl => <span key={cl} className="erb-chip erb-chip-clause">{cl}</span>)}
          </div>
        )}
        {onNavigateToControl && (
          <button
            type="button"
            className="erb-link-btn"
            onClick={() => { onClose(); onNavigateToControl(control.scf_id, 'in-scope') }}
          >Open {control.scf_id} in the Control Library</button>
        )}
        <div className="erb-dialog-body">
          <ReviewQueries
            organizationId={organizationId}
            engagementId={engagementId}
            target={{ scfId: control.scf_id }}
            viewer={viewer}
            onChanged={onChanged}
            inDialog
          />
        </div>
      </div>
    </div>
  )
}
