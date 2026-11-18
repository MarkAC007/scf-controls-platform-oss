// SPDX-License-Identifier: AGPL-3.0-only
/** The band under the register: proposed starters waiting for a team and a target, with today's values. */
import type { JSX } from 'react'
import type { ObjectiveOut } from '../../../data/securityObjectivesApi'

interface Props {
  items: ObjectiveOut[]
  isAdmin: boolean
  onConfirm: (o: ObjectiveOut) => void
}

export default function ProposedBand({ items, isAdmin, onConfirm }: Props): JSX.Element | null {
  if (items.length === 0) return null
  const n = items.length
  return (
    <div className="obj-band" data-testid="proposed-band">
      <p>
        <strong>{n} proposed {n === 1 ? 'objective is' : 'objectives are'}</strong> waiting for a team and a
        target. Today {n === 1 ? 'it reads' : 'they read'}:
      </p>
      <ul className="obj-band-items">
        {items.map((o) => (
          <li key={o.id} className="obj-band-item">
            <span>{o.name}</span>
            <b>{o.reading.value_text || '—'}</b>
            {isAdmin && (
              <button
                type="button"
                className="btn-link"
                aria-label={`Review and confirm ${o.name}`}
                onClick={() => onConfirm(o)}
              >
                Review and confirm
              </button>
            )}
          </li>
        ))}
      </ul>
    </div>
  )
}
