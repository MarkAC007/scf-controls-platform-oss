// SPDX-License-Identifier: AGPL-3.0-only
/**
 * Control maturity against its target, one row per domain.
 *
 * Each row is a bullet on the L0 to L5 scale: the band behind is the domain's
 * target (Settings, Maturity targets), the bar inside it is the average level
 * its controls have reached, and the tick marks the target so it stays
 * visible when the bar has passed it. The average can sit above the target
 * while single controls are still below it, so the count beside the bar is
 * the figure that says whether the domain is there.
 */
import { useState } from 'react'
import type { CSSProperties } from 'react'
import type { DomainTargetStanding, TargetStanding } from '../../data/targetMaturity'

const SCALE = ['L0', 'L1', 'L2', 'L3', 'L4', 'L5'] as const
const COLLAPSED_ROWS = 6

/** A level, or an average of levels, as a distance along the L0 to L5 track. */
const along = (level: number): string => `${Math.max(0, Math.min(5, level)) * 20}%`

const levelNumber = (level: string): number => Number(level.slice(1))

function rowLabel(row: DomainTargetStanding): string {
  const average = row.average === null ? 'no control rated' : `average level ${row.average.toFixed(1)}`
  const target = row.target ? `target ${row.target}` : 'targets set on single controls'
  return `${row.name}: ${average}, ${target}, ${row.atTarget} of ${row.withTarget} controls at target`
}

function Row({ row }: { row: DomainTargetStanding }): JSX.Element {
  const met = row.atTarget === row.withTarget
  const position = {
    '--mtarget-target': row.target ? along(levelNumber(row.target)) : '0%',
    '--mtarget-actual': along(row.average ?? 0),
  } as CSSProperties
  return (
    <li className={met ? 'mtarget-row mtarget-row--met' : 'mtarget-row mtarget-row--below'} data-testid={`mtarget-row-${row.code}`}>
      <span className="mtarget-domain" title={row.name}>
        <strong>{row.code}</strong>
        <span className="mtarget-domain-name">{row.name}</span>
      </span>
      <span className="mtarget-track" role="img" aria-label={rowLabel(row)} style={position}>
        {row.target && <span className="mtarget-shadow" />}
        {row.average !== null && <span className="mtarget-fill" />}
        {row.target && <span className="mtarget-mark" />}
      </span>
      <span className="mtarget-figures">
        <strong>{row.average === null ? '–' : row.average.toFixed(1)}</strong>
        <span className="mtarget-figures-target">{row.target ? `of ${row.target}` : 'own targets'}</span>
      </span>
      <span className="mtarget-count">
        {row.atTarget} of {row.withTarget}
      </span>
    </li>
  )
}

export default function TargetStandingBlock({ standing }: { standing: TargetStanding }): JSX.Element {
  const [showAll, setShowAll] = useState(false)
  const rows = showAll ? standing.domains : standing.domains.slice(0, COLLAPSED_ROWS)
  const hidden = standing.domains.length - COLLAPSED_ROWS

  return (
    <section className="dashboard-card mtarget" aria-labelledby="mtarget-title">
      <div className="dashboard-card-header mtarget-head">
        <h3 id="mtarget-title">Control maturity against target</h3>
        {standing.withTarget > 0 && (
          <span className="mtarget-headline">
            <strong>{standing.atTarget} of {standing.withTarget}</strong> controls with a target have reached it
          </span>
        )}
      </div>

      {standing.withTarget === 0 ? (
        <p className="mtarget-note">
          No control has a target maturity yet. Set one for each domain in Settings, under Maturity targets.
        </p>
      ) : (
        <>
          <div className="mtarget-key" aria-hidden="true">
            <span className="mtarget-key-item"><span className="mtarget-key-swatch mtarget-key-swatch--shadow" />Target</span>
            <span className="mtarget-key-item"><span className="mtarget-key-swatch mtarget-key-swatch--met" />Average level, all at target</span>
            <span className="mtarget-key-item"><span className="mtarget-key-swatch mtarget-key-swatch--below" />Average level, some below</span>
          </div>
          <div className="mtarget-scale" aria-hidden="true">
            <span className="mtarget-scale-ticks">
              {SCALE.map(level => <span key={level}>{level}</span>)}
            </span>
            <span className="mtarget-scale-count">At target</span>
          </div>
          <ul className="mtarget-rows">
            {rows.map(row => <Row key={row.code} row={row} />)}
          </ul>
          {hidden > 0 && (
            <button type="button" className="gaps-view-all" onClick={() => setShowAll(prev => !prev)}>
              {showAll ? `Show the ${COLLAPSED_ROWS} furthest behind` : `Show all ${standing.domains.length} domains`}
            </button>
          )}
          {standing.noTarget > 0 && (
            <p className="mtarget-note">
              {standing.noTarget} {standing.noTarget === 1 ? 'control has' : 'controls have'} no target
              {standing.domainsWithoutTarget > 0 && `, ${standing.domainsWithoutTarget} ${standing.domainsWithoutTarget === 1 ? 'domain' : 'domains'} with none set`}.
              Targets are set in Settings, under Maturity targets.
            </p>
          )}
        </>
      )}
    </section>
  )
}
