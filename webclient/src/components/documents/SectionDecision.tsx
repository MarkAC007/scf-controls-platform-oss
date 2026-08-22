/**
 * The decision controls for a section the merge could not settle on its own.
 *
 * Two statuses need a human answer and, until now, neither had a control:
 *
 *   - `conflict`. Both you and the generator changed the section. Your text was
 *     kept, and the only way to clear the status was to retype your own words
 *     over themselves. The banner said the generated alternative was "in this
 *     document's version history", where history showed version numbers and
 *     nothing else. A dead end with a footnote.
 *   - `pending_retirement`. The controls behind the section have left scope.
 *     The in-document marker instructed the reader to "retire it deliberately"
 *     and offered nothing to press.
 *
 * The wording matters as much as the buttons. A conflicted section is not
 * merely "different" — it is usually a passage written against an earlier
 * scope, which is how a document ends up claiming 345 controls in its masthead
 * and 1390 in section 2. Nothing can auto-correct that; the fix is to say
 * plainly what the divergence is and let the reader settle it.
 *
 * Retirement is the one destructive action here, so it takes two presses. It is
 * confirmed in the page rather than through `window.confirm`: a native modal
 * blocks the whole renderer, cannot be styled or themed, and wedges the
 * browser-automation used to verify this feature.
 */
import { useState } from 'react'
import type { DocumentSection, SectionResolveChoice } from '../../data/documentsApi'

interface Props {
  section: DocumentSection
  /** A resolve call is in flight — for this section or any other. */
  pending: boolean
  onResolve: (choice: SectionResolveChoice) => void
  /** Open the side-by-side diff. Omitted where there is nowhere to open it. */
  onCompare?: () => void
  /** Published documents are read-only; the controls show but do not fire. */
  disabled?: boolean
  /**
   * `flow` sits inside the document body in the reader; `bar` sits beside
   * "Save section" in the editor and drops the explanatory sentence, which the
   * editor already carries above the textarea.
   */
  variant?: 'flow' | 'bar'
}

const EXPLANATION: Record<string, string> = {
  conflict:
    'You and the generator both changed this section. Your text was kept, but it was ' +
    'written against an earlier scope — so it can contradict the rest of the document. ' +
    'Compare the two and choose which one is the policy.',
  pending_retirement:
    'The controls behind this section have left scope, so the generator no longer ' +
    'produces it. Retiring removes it from the document; the text stays in version ' +
    'history either way.',
}

export default function SectionDecision({
  section,
  pending,
  onResolve,
  onCompare,
  disabled = false,
  variant = 'flow',
}: Props) {
  const [confirmingRetire, setConfirmingRetire] = useState(false)

  if (section.status !== 'conflict' && section.status !== 'pending_retirement') {
    return null
  }

  const busy = pending || disabled

  return (
    <div className={`doc-decision doc-decision-${variant} status-${section.status}`}>
      {variant === 'flow' && (
        <p className="doc-decision-text">{EXPLANATION[section.status]}</p>
      )}

      {confirmingRetire ? (
        <div className="doc-decision-confirm">
          <span>
            Retire “{section.heading_text}”? It leaves the document. The text stays
            recoverable from version history.
          </span>
          <div className="doc-decision-actions">
            <button
              type="button"
              className="btn-danger"
              disabled={busy}
              onClick={() => {
                setConfirmingRetire(false)
                onResolve('retire')
              }}
            >
              Yes, retire it
            </button>
            <button
              type="button"
              className="btn-secondary"
              onClick={() => setConfirmingRetire(false)}
            >
              Cancel
            </button>
          </div>
        </div>
      ) : (
        <div className="doc-decision-actions">
          {section.status === 'conflict' ? (
            <>
              <button
                type="button"
                className="btn-primary"
                disabled={busy}
                onClick={() => onResolve('keep_mine')}
              >
                Keep mine
              </button>
              <button
                type="button"
                className="btn-secondary"
                disabled={busy}
                onClick={() => onResolve('take_generated')}
              >
                Take generated
              </button>
            </>
          ) : (
            <>
              <button
                type="button"
                className="btn-secondary"
                disabled={busy}
                onClick={() => setConfirmingRetire(true)}
              >
                Retire
              </button>
              <button
                type="button"
                className="btn-primary"
                disabled={busy}
                onClick={() => onResolve('keep')}
              >
                Keep
              </button>
            </>
          )}
          {onCompare && (
            <button type="button" className="btn-secondary" onClick={onCompare}>
              Compare
            </button>
          )}
        </div>
      )}
    </div>
  )
}
