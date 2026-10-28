/**
 * Editable rows of an ISMS register.
 *
 * Each row is a small form rather than a table row: most fields are sentences
 * (an issue, a requirement, how it is addressed), and a table of textareas is
 * unreadable at any width a laptop has. Fields lay out on a grid that wraps,
 * so a row is two or three lines on a wide screen and one column on a phone.
 */
import type { ReactNode } from 'react'

export type FieldType = 'text' | 'textarea' | 'select' | 'checkbox' | 'date'

export interface RegisterField<T> {
  key: keyof T & string
  label: string
  type: FieldType
  options?: { value: string; label: string }[]
  placeholder?: string
  /** Spans the full row width (long text). */
  wide?: boolean
}

interface Props<T extends { id?: string }> {
  rows: T[]
  fields: RegisterField<T>[]
  onChange: (rows: T[]) => void
  blank: () => T
  disabled?: boolean
  /** Names one row in labels and buttons, e.g. "issue". */
  noun: string
  empty: ReactNode
}

/**
 * Splits fields into consecutive runs of short (select, text, date, checkbox)
 * and long (textarea) fields, keeping their order. Each run gets its own grid:
 * on one shared grid the short fields set the column count, and a textarea
 * landed in a single narrow column with its text hidden behind a scrollbar.
 */
function fieldRuns<T>(fields: RegisterField<T>[]) {
  const runs: { long: boolean; fields: RegisterField<T>[] }[] = []
  for (const field of fields) {
    const long = field.type === 'textarea'
    const last = runs[runs.length - 1]
    if (last && last.long === long) last.fields.push(field)
    else runs.push({ long, fields: [field] })
  }
  return runs
}

export default function RegisterRows<T extends { id?: string }>({
  rows, fields, onChange, blank, disabled = false, noun, empty,
}: Props<T>) {
  const update = (index: number, key: keyof T, value: unknown) =>
    onChange(rows.map((row, i) => (i === index ? { ...row, [key]: value } : row)))
  const runs = fieldRuns(fields)

  return (
    <div className="isms-rows">
      {rows.length === 0 && <p className="isms-empty">{empty}</p>}
      {rows.map((row, index) => (
        <fieldset key={row.id ?? `new-${index}`} className="isms-row" disabled={disabled}>
          <legend className="isms-row-legend">
            {noun.charAt(0).toUpperCase() + noun.slice(1)} {index + 1}
          </legend>
          {runs.map((run, runIndex) => (
          <div key={runIndex} className={`isms-row-fields ${run.long ? 'is-long' : ''}`}>
            {run.fields.map((field) => {
              const id = `isms-${noun.replace(/\s+/g, '-')}-${index}-${field.key}`
              const value = row[field.key] as unknown
              if (field.type === 'checkbox') {
                return (
                  <label key={field.key} className="isms-check" htmlFor={id}>
                    <input
                      id={id}
                      type="checkbox"
                      checked={Boolean(value)}
                      onChange={(e) => update(index, field.key, e.target.checked)}
                    />
                    <span>{field.label}</span>
                  </label>
                )
              }
              return (
                <label
                  key={field.key}
                  className={`isms-field ${field.wide ? 'is-wide' : ''}`}
                  htmlFor={id}
                >
                  <span>{field.label}</span>
                  {field.type === 'textarea' ? (
                    <textarea
                      id={id}
                      rows={3}
                      value={(value as string) ?? ''}
                      placeholder={field.placeholder}
                      onChange={(e) => update(index, field.key, e.target.value)}
                    />
                  ) : field.type === 'select' ? (
                    <select
                      id={id}
                      value={(value as string) ?? ''}
                      onChange={(e) => update(index, field.key, e.target.value)}
                    >
                      {field.options?.map((o) => (
                        <option key={o.value} value={o.value}>{o.label}</option>
                      ))}
                    </select>
                  ) : (
                    <input
                      id={id}
                      type={field.type === 'date' ? 'date' : 'text'}
                      value={(value as string) ?? ''}
                      placeholder={field.placeholder}
                      onChange={(e) =>
                        update(index, field.key, field.type === 'date' && !e.target.value ? null : e.target.value)
                      }
                    />
                  )}
                </label>
              )
            })}
          </div>
          ))}
          <button
            type="button"
            className="btn-link isms-row-remove"
            onClick={() => onChange(rows.filter((_, i) => i !== index))}
          >
            Remove {noun}
          </button>
        </fieldset>
      ))}
      {!disabled && (
        <button type="button" className="btn-secondary isms-add" onClick={() => onChange([...rows, blank()])}>
          Add {noun}
        </button>
      )}
    </div>
  )
}
