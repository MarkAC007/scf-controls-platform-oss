// SPDX-License-Identifier: AGPL-3.0-only
/**
 * One objective on the ISMS metrics dashboard. The title is the link to the
 * objective's metric detail screen; it stretches over the tile, so the whole
 * tile still opens it, with one tab stop and one name for a screen reader. The value is the server's `value_text`
 * (it owns the rounding); a trailing % is drawn small, never recomputed.
 */
import type { CSSProperties, JSX } from 'react'
import type { ObjectiveOut } from '../../data/securityObjectivesApi'
import AppLink from '../AppLink'
import ClauseChips from '../isms/objectives/ClauseChips'
import StatusChip from '../isms/objectives/StatusChip'
import { kindLabel, readingAside, targetLine } from '../isms/objectives/objectiveText'
import Sparkline from './Sparkline'

const clamp = (n: number) => Math.max(0, Math.min(100, n))

function TileValue({ objective }: { objective: ObjectiveOut }): JSX.Element {
  const { metric, reading } = objective
  if (!metric || metric.kind === 'unset') {
    return <div className="ismsm-tile-value ismsm-tile-value--text ismsm-tile-value--dim">Choose a metric</div>
  }
  const text = reading.value_text || '—'
  if (reading.value === null) {
    const isWord = text !== '—'
    return (
      <div className={`ismsm-tile-value${isWord ? ' ismsm-tile-value--text' : ' ismsm-tile-value--dim'}`}>{text}</div>
    )
  }
  if (metric.kind === 'milestone') {
    return <div className="ismsm-tile-value ismsm-tile-value--text">{text}</div>
  }
  if (text.endsWith('%')) {
    return (
      <div className="ismsm-tile-value">
        {text.slice(0, -1)}
        <small>%</small>
      </div>
    )
  }
  // A number with words after it ("1 of 4 audits") reads better smaller.
  return <div className={`ismsm-tile-value${/^[\d.,-]+$/.test(text) ? '' : ' ismsm-tile-value--text'}`}>{text}</div>
}

export default function MetricTile({ objective }: { objective: ObjectiveOut }): JSX.Element {
  const { metric, reading } = objective
  const showMeter = metric?.unit === 'percent' && metric.kind !== 'unset' && reading.value !== null
  const target = metric?.target_value ?? null
  const meterStyle = {
    '--ismsm-fill': `${clamp(reading.value ?? 0)}%`,
    ...(target !== null ? { '--ismsm-target': `${clamp(target)}%` } : {}),
  } as CSSProperties
  const aside = readingAside(objective)
  const line = targetLine(objective)
  // An older server sends no pace: read it as none.
  const pace = reading.pace ?? null

  return (
    <article className={`ismsm-tile ismsm-tile--${reading.status}`} data-testid={`metric-tile-${objective.ref}`}>
      <div className="ismsm-tile-top">
        <span className="obj-ref">{objective.ref}</span>
        <span className="ismsm-kind">{kindLabel(metric)}</span>
      </div>
      <h3 className="ismsm-tile-name">
        <AppLink to={{ kind: 'isms-metric', id: objective.id }} className="ismsm-tile-link">
          {objective.name}
        </AppLink>
      </h3>
      <div className="ismsm-tile-mid">
        <TileValue objective={objective} />
        <Sparkline objective={objective} />
      </div>
      {showMeter && (
        <div
          className="ismsm-meter"
          role="img"
          aria-label={`${reading.value_text}${target !== null ? ` against a target of ${target}%` : ''}`}
          style={meterStyle}
        >
          <i />
          {target !== null && <b />}
        </div>
      )}
      {line && <p className="ismsm-tile-target">{line}</p>}
      <div className="ismsm-tile-status">
        <StatusChip status={reading.status} daysLeft={reading.days_left} />
        {(pace || aside) && (
          <span>
            {pace && (
              <span className={`ismsm-pace ismsm-pace--${pace.state}`} data-testid="tile-pace">
                {pace.label}
              </span>
            )}
            {pace && aside && ' · '}
            {aside}
          </span>
        )}
      </div>
      <div className="ismsm-tile-foot">
        <span>{objective.owning_team_name ?? 'No team yet'}</span>
        <ClauseChips clauses={objective.iso_clauses} compact />
      </div>
    </article>
  )
}
