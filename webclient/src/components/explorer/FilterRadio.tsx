/**
 * FilterRadio — radio-group filter primitive for Explorer sidebars.
 *
 * Semantically a <radiogroup>; visual style matches the ScopingList scope
 * radio (scoping-scope-radio-*) generalised to explorer tokens.
 *
 * Props:
 *   label    — accessible group label (also shown as the group aria-label)
 *   options  — array of { value, label } pairs
 *   value    — currently selected value
 *   onChange — called with the new value when the user selects a different option
 *   name     — optional HTML name attribute; auto-generated from label if omitted
 */
import type { JSX } from 'react'

export interface FilterRadioOption {
  value: string
  label: string
}

interface FilterRadioProps {
  label: string
  options: FilterRadioOption[]
  value: string
  onChange: (value: string) => void
  name?: string
}

export default function FilterRadio({
  label,
  options,
  value,
  onChange,
  name,
}: FilterRadioProps): JSX.Element {
  // Derive a stable name from the label when none is supplied
  const groupName = name ?? `filter-radio-${label.toLowerCase().replace(/\s+/g, '-')}`

  return (
    <div
      className="explorer-filter-radio-group"
      role="radiogroup"
      aria-label={label}
    >
      {options.map((opt) => (
        <label key={opt.value} className="explorer-filter-radio">
          <input
            type="radio"
            name={groupName}
            value={opt.value}
            checked={value === opt.value}
            onChange={() => onChange(opt.value)}
          />
          <span className="explorer-filter-radio-dot" aria-hidden="true" />
          <span className="explorer-filter-radio-label">{opt.label}</span>
        </label>
      ))}
    </div>
  )
}
