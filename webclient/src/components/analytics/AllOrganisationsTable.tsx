// SPDX-License-Identifier: AGPL-3.0-only
/**
 * AllOrganisationsTable — the Analytics "All organisations" sub-tab (phase 3 B3): one row of
 * summary counts per organisation the person can open, from the server's stored rows.
 *
 * The counts and their words are the summary strip's, from the same keys; nothing is
 * recomputed here. "Open" hands the organisation to AnalyticsPage, which switches to it and
 * opens ISMS metrics.
 */
import { type JSX } from 'react'
import { useQuery } from '@tanstack/react-query'
import { getIsmsPortfolio, type Portfolio, type PortfolioItem } from '../../data/securityObjectivesApi'
import { formatDate } from '../isms/objectives/objectiveText'

interface Props {
  currentOrganizationId: string
  onOpen: (organizationId: string) => void
}

/** The summary strip's status columns, in its order and with its words. */
const COUNTS: { key: 'on_track' | 'at_risk' | 'off_track' | 'needs_attention'; label: string }[] = [
  { key: 'on_track', label: 'On track' },
  { key: 'at_risk', label: 'At risk' },
  { key: 'off_track', label: 'Off track' },
  { key: 'needs_attention', label: 'Needs attention' },
]

const staleNote = (it: PortfolioItem) =>
  it.last_measured_on ? `Not measured since ${formatDate(it.last_measured_on)}` : 'No stored measurement yet'

export default function AllOrganisationsTable({ currentOrganizationId, onOpen }: Props): JSX.Element {
  const portfolio = useQuery<Portfolio>({
    queryKey: ['isms-metrics-portfolio'],
    queryFn: () => getIsmsPortfolio(),
    staleTime: 0,
  })

  if (portfolio.isPending) return <p className="gov-muted ismsm-pf-state">Loading organisations…</p>
  if (portfolio.isError) {
    return (
      <div role="alert" className="gov-error ismsm-pf-state">
        Could not load the organisations&apos; summaries.{' '}
        <button type="button" className="btn-secondary" onClick={() => void portfolio.refetch()}>Try again</button>
      </div>
    )
  }
  const items = portfolio.data.items
  if (items.length === 0) return <p className="gov-muted ismsm-pf-state">No organisations to show.</p>

  return (
    <div className="cp-container ismsm ismsm-pf">
      <div className="ismsm-table-wrap ismsm-pf-wrap">
        <table className="ismsm-table ismsm-pf-table">
          <caption className="ismsm-pf-caption">Security objectives by organisation, from the last stored measurements</caption>
          <thead>
            <tr>
              <th scope="col">Organisation</th>
              <th scope="col">Objectives</th>
              {COUNTS.map((c) => <th key={c.key} scope="col">{c.label}</th>)}
              <th scope="col">Last measured</th>
              <th scope="col"><span className="ismsm-sr-only">Open</span></th>
            </tr>
          </thead>
          <tbody>
            {items.map((it) => {
              const current = it.organization_id === currentOrganizationId
              return (
                <tr key={it.organization_id} aria-current={current ? 'true' : undefined} className={current ? 'ismsm-pf-current' : undefined}>
                  <th scope="row">
                    {it.organization_name}
                    {current && <span className="ismsm-pf-tag">Current</span>}
                  </th>
                  <td className="ismsm-pf-num">{it.objectives}</td>
                  {COUNTS.map((c) => (
                    <td key={c.key} className={`ismsm-pf-num${it[c.key] > 0 ? ` ismsm-pf-count--${c.key}` : ''}`}>{it[c.key]}</td>
                  ))}
                  <td>
                    {it.last_measured_on ? formatDate(it.last_measured_on) : '—'}
                    {it.stale && <span className="ismsm-log-note">{staleNote(it)}</span>}
                  </td>
                  <td>
                    <button type="button" className="btn-secondary ismsm-pf-open" onClick={() => onOpen(it.organization_id)}>
                      Open<span className="ismsm-sr-only"> {it.organization_name}</span>
                    </button>
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
    </div>
  )
}
