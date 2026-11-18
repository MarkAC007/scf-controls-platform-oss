// SPDX-License-Identifier: AGPL-3.0-only
import type { JSX } from 'react'

interface Props {
  clauses: string[]
  /** When given, each chip carries a remove button. */
  onRemove?: (clause: string) => void
  /** Collapse runs of consecutive Annex A controls into one chip ("A.5.24–30"), as the dashboard tile does. */
  compact?: boolean
}

export interface ClauseRun { text: string; clauses: string[] }

const annex = (c: string) => /^A\.(\d+)\.(\d+)$/i.exec(c.trim())

/**
 * Runs of consecutive Annex A controls in the order given (A.5.24, A.5.25 … A.5.30) become one
 * "A.5.24–30"; anything else stays as it is. Never reorders.
 */
export function clauseRuns(clauses: string[]): ClauseRun[] {
  const runs: ClauseRun[] = []
  for (const c of clauses) {
    const m = annex(c)
    const last = runs[runs.length - 1]
    const prev = last && annex(last.clauses[last.clauses.length - 1])
    if (m && prev && prev[1] === m[1] && Number(m[2]) === Number(prev[2]) + 1) {
      last.clauses.push(c)
      const first = annex(last.clauses[0])!
      last.text = `A.${first[1]}.${first[2]}–${m[2]}`
    } else {
      runs.push({ text: c, clauses: [c] })
    }
  }
  return runs
}

/** ISO/IEC 27001 clause references as small mono chips. */
export default function ClauseChips({ clauses, onRemove, compact = false }: Props): JSX.Element | null {
  if (clauses.length === 0) return null
  if (compact && !onRemove) {
    return (
      <span className="obj-clauses">
        {clauseRuns(clauses).map((run, i) =>
          run.clauses.length === 1 ? (
            <span key={i} className="obj-clause">{run.text}</span>
          ) : (
            <span key={i} className="obj-clause">
              <span aria-hidden="true">{run.text}</span>
              <span className="ismsm-sr-only">{run.clauses.join(', ')}</span>
            </span>
          ),
        )}
      </span>
    )
  }
  return (
    <span className="obj-clauses">
      {clauses.map((c) => (
        <span key={c} className="obj-clause">
          {c}
          {onRemove && (
            <button type="button" className="obj-clause-remove" aria-label={`Remove clause ${c}`} onClick={() => onRemove(c)}>
              ×
            </button>
          )}
        </span>
      ))}
    </span>
  )
}
