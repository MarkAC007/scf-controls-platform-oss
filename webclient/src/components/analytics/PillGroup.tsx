// SPDX-License-Identifier: AGPL-3.0-only
/** A filter as a row of pills (the mock-up's `.chip` row), shared by the ISMS metrics dashboard and the objectives register. */
import { useId, type JSX } from 'react'

export interface PillOption { value: string; label: string }

/**
 * One filter as a row of pills, as in the mock-up: "All" plus one per option.
 * Each pill is a toggle button; the chosen one is pressed and filled.
 */
export default function PillGroup({ label, value, options, onChange }: {
  label: string
  value: string
  options: PillOption[]
  onChange: (value: string) => void
}): JSX.Element {
  const labelId = useId()
  // A value no pill names (an old or hand-typed URL) filters nothing, so "All" is the one pressed.
  const current = options.some((o) => o.value === value) ? value : ''
  const pill = (v: string, text: string) => (
    <button
      key={v || 'all'}
      type="button"
      className="ismsm-pill"
      aria-pressed={current === v}
      onClick={() => onChange(v)}
    >
      {text}
    </button>
  )
  return (
    <div className="ismsm-pills" role="group" aria-labelledby={labelId}>
      <span className="ismsm-pills-label" id={labelId}>{label}</span>
      {pill('', 'All')}
      {options.map((o) => pill(o.value, o.label))}
    </div>
  )
}
