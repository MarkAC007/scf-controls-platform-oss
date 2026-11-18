// SPDX-License-Identifier: AGPL-3.0-only
/**
 * ObjectiveSummaryStrip — the five figures that sum up the security
 * objectives: how many there are, and how many are on track, at risk, off
 * track or in need of attention. Shown at the head of ISMS metrics and, as
 * links into it, on the dashboard.
 */
import type { JSX } from 'react'
import type { ObjectiveSummary } from '../../data/securityObjectivesApi'
import AppLink from '../AppLink'
import { StatusIcon } from '../isms/objectives/StatusChip'
import { measuredCount } from '../isms/objectives/objectiveText'
import type { StatusIconName } from '../isms/objectives/objectiveText'

interface Card {
  key: string
  label: string
  icon?: StatusIconName
  value: number
  sub: string
  /** The status pill on ISMS metrics that lists what this figure counts; none for the total. */
  status?: string
}

const joinParts = (parts: [number, string][]) =>
  parts.filter(([n]) => n > 0).map(([n, label]) => `${n} ${label}`).join(' · ')

export function summaryCards(s: ObjectiveSummary): Card[] {
  return [
    {
      key: 'objectives', label: 'Objectives', value: s.objectives,
      sub: [
        `${measuredCount(s)} measured · ${s.proposed} proposed · ${s.not_measured} no metric`,
        joinParts([[s.achieved, 'achieved'], [s.missed, 'missed']]),
      ].filter(Boolean).join(' · '),
    },
    {
      key: 'on-track', label: 'On track', icon: 'check', value: s.on_track, status: 'on_track',
      sub: s.in_progress > 0 ? `plus ${s.in_progress} in progress, not yet due` : '',
    },
    { key: 'at-risk', label: 'At risk', icon: 'triangle', value: s.at_risk, status: 'at_risk', sub: 'inside the warning margin' },
    { key: 'off-track', label: 'Off track', icon: 'cross', value: s.off_track, status: 'off_track', sub: 'beyond the warning level' },
    {
      key: 'needs-attention', label: 'Needs attention', icon: 'circle', value: s.needs_attention, status: 'needs_attention',
      sub: joinParts([
        [s.proposed, 'to confirm'],
        [s.overdue, 'overdue'],
        [s.insufficient_data, 'thin data'],
        [s.not_measured, 'no metric'],
      ]),
    },
  ]
}

interface Props {
  summary: ObjectiveSummary
  /** Each figure opens ISMS metrics on the objectives it counts. */
  linked?: boolean
}

export default function ObjectiveSummaryStrip({ summary, linked = false }: Props): JSX.Element {
  return (
    <div className="kpi-row ismsm-strip" role="group" aria-label="Objectives summary">
      {summaryCards(summary).map((card) => {
        const body = (
          <>
            <div className="kpi-card-header">
              <span className="kpi-label">
                {card.icon && <StatusIcon name={card.icon} />}
                {card.label}
              </span>
            </div>
            <div className="kpi-value">{card.value}</div>
            {card.sub && <p className="ismsm-kpi-sub">{card.sub}</p>}
          </>
        )
        return linked ? (
          <AppLink
            key={card.key}
            to={{ kind: 'isms-metrics', status: card.status }}
            className="kpi-card ismsm-kpi-link"
            data-testid={`ismsm-kpi-${card.key}`}
          >
            {body}
          </AppLink>
        ) : (
          <div key={card.key} className="kpi-card" data-testid={`ismsm-kpi-${card.key}`}>
            {body}
          </div>
        )
      })}
    </div>
  )
}
