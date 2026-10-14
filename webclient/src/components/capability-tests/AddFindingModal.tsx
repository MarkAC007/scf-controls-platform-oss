/**
 * AddFindingModal — add a finding found after a capability test was recorded
 * (PIG R11). It is recorded as a deficiency linked to the test.
 *
 * A test recorded as passed found nothing, so the server refuses a finding on
 * it alone: the result is corrected with the finding in one request. Here
 * that is a required choice of the new result, sent with the finding as a
 * correction.
 *
 * Neither can be undone: a finding found in error is cancelled as a
 * deficiency, and a test is never deleted.
 *
 * Mounted only while open. It cannot be dismissed while a save is in flight,
 * and Escape or a backdrop click is ignored once anything is typed; Cancel or
 * ✕ still close it.
 */
import { useId, useRef, useState, type JSX } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { toast } from 'react-hot-toast'
import { addCapabilityTestFinding, updateCapabilityTest } from '../../data/apiClient'
import { invalidateDeficiencies } from '../../hooks/useDeficiencies'
import { useDialogFocus } from '../../hooks/useDialogFocus'
import { useModalDismiss } from '../../hooks/useModalDismiss'
import type { CapabilityTest, CapabilityTestResult } from '../../types'
import { RESULT_LABELS } from './capabilityTestText'
import FindingFields, { emptyFinding, findingBody, findingTouched, type FindingDraft } from './FindingFields'

interface Props {
  organizationId: string
  test: CapabilityTest
  onClose: () => void
}

const CORRECTED_RESULTS: CapabilityTestResult[] = ['passed_with_findings', 'failed']

const errorText = (err: unknown) => (err instanceof Error ? err.message : String(err))

export default function AddFindingModal({ organizationId, test, onClose }: Props): JSX.Element {
  const queryClient = useQueryClient()
  const dialogRef = useRef<HTMLDivElement>(null)
  useDialogFocus(dialogRef)
  const id = useId()

  const [finding, setFinding] = useState<FindingDraft>(emptyFinding)
  const [result, setResult] = useState<CapabilityTestResult | ''>('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const correcting = test.result === 'passed'
  const canSubmit = finding.title.trim() !== '' && (!correcting || result !== '') && !busy
  const dirty = findingTouched(finding) || result !== ''

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
      if (correcting && result) {
        await updateCapabilityTest(organizationId, test.id, { result, findings: [findingBody(finding)] })
        toast.success(`Finding added and the test corrected to ${RESULT_LABELS[result].toLowerCase()}`)
      } else {
        await addCapabilityTestFinding(organizationId, test.id, findingBody(finding))
        toast.success('Finding added as a deficiency')
      }
      await invalidateDeficiencies(queryClient, organizationId)
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
          <h2 id={`${id}-heading`}>Add finding</h2>
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
              <span className="deficiency-form-label">Capability test</span>
              <p className="deficiency-subject">{test.title}</p>
            </div>

            {correcting && (
              <div className="form-group">
                <label htmlFor={`${id}-result`}>The test's result becomes</label>
                <select
                  id={`${id}-result`}
                  className="form-control"
                  value={result}
                  disabled={busy}
                  aria-required="true"
                  aria-describedby={`${id}-result-hint`}
                  onChange={(e) => setResult(e.target.value as CapabilityTestResult | '')}
                >
                  <option value="">Choose…</option>
                  {CORRECTED_RESULTS.map((r) => (
                    <option key={r} value={r}>
                      {RESULT_LABELS[r]}
                    </option>
                  ))}
                </select>
                <p id={`${id}-result-hint`} className="deficiency-field-hint">
                  This test is recorded as passed, which means nothing was found. A finding corrects its result, and
                  both are saved together.
                </p>
              </div>
            )}

            <FindingFields organizationId={organizationId} value={finding} onChange={setFinding} busy={busy} autoFocus />

            <p className="deficiency-field-hint">
              It becomes a deficiency on the Deficiencies (POA&amp;M) page and cannot be deleted; one found in error is
              cancelled there.
            </p>
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
              {busy ? 'Saving…' : 'Add finding'}
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}
