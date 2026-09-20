import { useEffect, useId, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { useModalDismiss } from '../../hooks/useModalDismiss'
import './ControlStatusHelp.css'

// Adapted for control owners from docs/16-control-and-evidence-acceptance.md
// in MarkAC007/scf-onboarding-playbook. These are review guidelines, not API gates.
const STATUS_GUIDANCE = [
  ['Not Started', 'Implementation has not yet been accepted. The baseline may still be incomplete.'],
  ['In Progress', 'Work is under way. Record the owner, the gap, the action being taken and the target date.'],
  ['Ready for Review', 'The owner has submitted an operating description and supporting evidence. Acceptance is still pending; you can request review before implementation has been accepted.'],
  ['Implemented', 'The agreed control design operates. Retain approved documentation where needed. A reviewer has checked all applicable assessment objectives and inspected sufficient, relevant evidence of actual operation. Record any limitations.'],
  ['Monitored', 'The implemented control has a working evidence collection and review cadence, current coverage across the agreed observation period, exception handling and a named reviewer.'],
  ['At Risk', 'A material gap, failed operation, loss of ownership or evidence problem undermines the status claim. Record the impact and response.'],
  ['Deferred', 'The control applies but has not yet been delivered. Record the reason, an accountable risk decision, an owner and a target date. It remains a gap.'],
  ['Not Applicable', 'Document the facts showing why the control does not apply within the agreed business scope. Retain your organisation’s approval and review the decision when circumstances change.'],
] as const

export default function ControlStatusHelp() {
  const [open, setOpen] = useState(false)
  const dialogRef = useRef<HTMLDialogElement>(null)
  const id = useId()
  const close = () => setOpen(false)
  useModalDismiss(open, close)

  useEffect(() => {
    const dialog = dialogRef.current
    if (!dialog) return
    if (open) {
      dialog.showModal()
      dialog.scrollTop = 0
    } else if (dialog.open) {
      dialog.close()
    }
    return () => {
      if (dialog.open) dialog.close()
    }
  }, [open])

  return (
    <>
      <button
        type="button"
        className="control-status-help-button"
        aria-label="Help with control status"
        title="Control status guidance"
        aria-haspopup="dialog"
        aria-expanded={open}
        aria-controls={id}
        onClick={(event) => {
          event.stopPropagation()
          setOpen(true)
        }}
      >
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" aria-hidden="true">
          <circle cx="12" cy="12" r="9" />
          <path d="M9.5 9a2.5 2.5 0 0 1 5 0c0 1.7-2.5 2-2.5 4" />
          <path d="M12 16v.5" strokeLinecap="round" />
        </svg>
      </button>
      {createPortal(
        <dialog
          ref={dialogRef}
          id={id}
          className="control-status-help-dialog"
          aria-labelledby={`${id}-title`}
          aria-describedby={`${id}-intro`}
          onCancel={(event) => {
            event.preventDefault()
            close()
          }}
          onClose={close}
          onKeyDown={(event) => event.stopPropagation()}
          onClick={(event) => {
            event.stopPropagation()
            if (event.target === event.currentTarget) close()
          }}
        >
          {open && <div className="control-status-help-content">
            <header className="control-status-help-header">
              <h2 id={`${id}-title`}>Control status guidance</h2>
              <button type="button" className="btn btn-secondary btn-sm" onClick={close} autoFocus>
                Close
              </button>
            </header>
            <div className="control-status-help-body">
              <p id={`${id}-intro`}>
                Choose the status supported by your control’s current operation and review record.
                These statuses are not a required sequence. Saving a status does not verify the
                evidence or complete an acceptance review.
              </p>
              <dl className="control-status-help-definitions">
                {STATUS_GUIDANCE.map(([status, guidance]) => (
                  <div key={status}>
                    <dt>{status}</dt>
                    <dd>{guidance}</dd>
                  </div>
                ))}
              </dl>
              <details>
                <summary>Evidence to check before accepting a control</summary>
                <p>
                  Review the full set of applicable assessment objectives (AOs) from the control
                  catalogue. Record each objective’s applicability, outcome and supporting references.
                  Missing or incomplete catalogue data does not mean there are no objectives.
                </p>
                <ul>
                  <li><strong>Relevance:</strong> Evidence must demonstrate the objective within your business scope. An approved policy shows design; it does not show that an action happened.</li>
                  <li><strong>Source and integrity:</strong> Record the source system, version, extraction or filters, preparer and retrieval path. Check available validation findings.</li>
                  <li><strong>Evidence period:</strong> Record the effective start and end dates separately from the upload date. Keep unknown periods unknown; uploading an old report does not make it current.</li>
                  <li><strong>Population and sampling:</strong> Record the population, sample selection and size, rationale, exclusions and exceptions. Match review depth to risk and the assessment purpose.</li>
                  <li><strong>Actual operation:</strong> Compare what happened with the defined process. Inspect sources or repeat checks where needed, and record deviations and remediation.</li>
                  <li><strong>Review conclusion:</strong> Record whether the design is adequate, inadequate or not assessed, and whether operation is supported, partially supported, unsupported or not yet observed. Include the reviewer, date, evidence references and next action.</li>
                </ul>
                <p>
                  Evidence descriptions and preparer assertions need review. Reconcile AI findings
                  with inspected evidence. Retain a linked review record for conclusions the platform
                  cannot fully capture. One file or a green badge is not enough to establish implementation.
                </p>
              </details>
              <details>
                <summary>Status, maturity and evidence health measure different things</summary>
                <p>
                  Assess control maturity against the applicable catalogue guidance for L0–L5,
                  record your rationale and agree a suitable target for each area. Your organisation’s
                  control maturity, computed evidence collection maturity and AI assessments are
                  separate measures. Two successful collections do not establish L3, and automation
                  alone does not establish an effective control.
                </p>
                <p>
                  Freshness describes collection timing; validation checks specific rules; AI provides
                  an advisory judgement. Aggregate scores do not independently prove compliance or
                  operation across an audit period. Check the actual evidence period: partial overlap
                  does not establish full-period coverage. Preserve the evidence versions supporting
                  a dated conclusion.
                </p>
              </details>
              <details>
                <summary>Gaps, exceptions and reassessment</summary>
                <p>
                  Record the control, failed criterion, impact, owner, treatment or compensating action,
                  authorised decision and review or expiry date. Escalate material risk promptly under
                  your agreed response expectations. An accepted exception can leave the control
                  In Progress, Deferred or At Risk; risk acceptance does not establish support.
                </p>
                <p>
                  Reopen review when evidence fails, scope or procedures change, objectives change,
                  or an exception expires. Preserve the previous conclusion and explain why it needs
                  reassessment. A scheduled activity that has never occurred is not yet observed.
                  Closing a remediation task alone does not establish that a finding is resolved:
                  inspect new evidence and record the reviewer’s accepted retest outcome.
                </p>
              </details>
              <details>
                <summary>Complete review with or without AI</summary>
                <p>
                  Reconcile every expected objective, including those omitted from AI input and
                  controls reviewed without AI. Record whether each current conclusion is supported,
                  shows a gap, cannot yet be reached, or has justified non-applicability. Keep
                  unreviewed and stale objectives visible. Missing inventory prevents a claim of
                  complete review; where the catalogue genuinely has no objectives, agree and
                  document alternative criteria with clearly local identifiers.
                </p>
                <p>
                  Review coverage measures completed human decisions, including gaps and uncertain
                  outcomes; it is not a readiness score. Report supported outcomes and justified
                  non-applicability separately. Keep risk acceptance and compensation decisions
                  separate from objective conclusions, and retain the review record and references.
                </p>
              </details>
            </div>
          </div>}
        </dialog>,
        document.body,
      )}
    </>
  )
}
