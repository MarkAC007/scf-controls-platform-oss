// SPDX-License-Identifier: AGPL-3.0-only
/**
 * IsmsMetricsDashboard — how the ISMS is performing against the
 * organisation's security objectives: a summary strip of five and one tile
 * per unarchived objective. Every value is worked out on the server on read.
 *
 * "Add objective" opens the same form as the register, in place; its save
 * refreshes both screens.
 *
 * Filters (team, clause group, status) are held in the URL by AnalyticsPage
 * and passed in. Team and clause narrow the strip and the tiles; status the
 * tiles only.
 */
import { useId, useState, type JSX } from 'react'
import { useIsOrgEditor } from '../../hooks/useHasOrgRole'
import { useIsmsMetrics } from '../../hooks/useSecurityObjectives'
import { navigateToDestination } from '../AppLink'
import ObjectiveFormModal from '../isms/objectives/ObjectiveFormModal'
import { errorText, formatDateTime } from '../isms/objectives/objectiveText'
import MetricTile from './MetricTile'
import ObjectiveSummaryStrip from './ObjectiveSummaryStrip'
import PillGroup from './PillGroup'
import {
  applyFilters,
  CLAUSE_GROUPS,
  isFiltered,
  NO_FILTERS,
  STATUS_FILTERS,
  summarise,
  teamOptions,
  type MetricFilters,
} from './ismsMetricFilters'

interface Props {
  organizationId: string
  filters?: MetricFilters
  onFilters?: (next: MetricFilters) => void
}

export default function IsmsMetricsDashboard({ organizationId, filters = NO_FILTERS, onFilters }: Props): JSX.Element {
  const canEdit = useIsOrgEditor(organizationId)
  const metrics = useIsmsMetrics(organizationId)
  const [adding, setAdding] = useState(false)
  const data = metrics.data
  const items = data?.items ?? []
  const shown = applyFilters(items, filters)
  const teamChoices = teamOptions(items, filters.team)
  // Unfiltered, the strip is the server's own summary; narrowed by team or clause, it counts what is shown.
  const summary = data && (filters.team || filters.clause) ? summarise(shown.strip) : data?.summary
  const setFilter = (key: keyof MetricFilters, value: string) => onFilters?.({ ...filters, [key]: value })
  const filterId = useId()

  return (
    <div className="cp-container ismsm">
      <div className="ismsm-head">
        <p className="page-subtitle">How the ISMS is performing against the organisation&apos;s security objectives.</p>
        <div className="ismsm-head-actions">
          {data && <span className="ismsm-measured">Measured {formatDateTime(data.measured_at)}</span>}
          <button
            type="button"
            className="btn-secondary"
            onClick={() => navigateToDestination({ kind: 'objectives-register' })}
          >
            Open objectives register
          </button>
          {canEdit && (
            <button type="button" className="btn-primary" onClick={() => setAdding(true)}>
              Add objective
            </button>
          )}
        </div>
      </div>

      {summary && <ObjectiveSummaryStrip summary={summary} />}

      {data && items.length > 0 && (
        <div className="ismsm-filters" role="group" aria-label="Filter objectives">
          <label className="ismsm-filter" htmlFor={`${filterId}-team`}>
            <span>Team</span>
            <select id={`${filterId}-team`} value={filters.team} onChange={(e) => setFilter('team', e.target.value)}>
              <option value="">All teams</option>
              {teamChoices.map((t) => <option key={t.value} value={t.value}>{t.label}</option>)}
            </select>
          </label>
          <PillGroup
            label="Clause"
            value={filters.clause}
            onChange={(v) => setFilter('clause', v)}
            options={CLAUSE_GROUPS.map((g) => ({ value: g.value, label: g.pill }))}
          />
          <PillGroup
            label="Status"
            value={filters.status}
            onChange={(v) => setFilter('status', v)}
            options={STATUS_FILTERS.map((f) => ({ value: f.value, label: f.label }))}
          />
          {isFiltered(filters) && (
            <button type="button" className="btn-link ismsm-filter-clear" onClick={() => onFilters?.(NO_FILTERS)}>
              Clear filters
            </button>
          )}
        </div>
      )}

      {metrics.isLoading ? (
        <div className="ismsm-grid" aria-busy="true">
          {Array.from({ length: 6 }).map((_, i) => (
            <div key={i} className="ismsm-tile cp-skeleton">
              <div className="cp-skeleton-line cp-skeleton-title" />
              <div className="cp-skeleton-line cp-skeleton-bar" />
              <div className="cp-skeleton-line cp-skeleton-footer" />
            </div>
          ))}
        </div>
      ) : metrics.isError ? (
        <div className="cp-error" role="alert">The ISMS metrics could not be loaded: {errorText(metrics.error)}</div>
      ) : data && data.items.length === 0 ? (
        <div className="cp-empty">
          <h3>No objectives yet</h3>
          <p>Add an objective to start measuring.</p>
        </div>
      ) : data && shown.tiles.length === 0 ? (
        <div className="cp-empty" data-testid="ismsm-no-match">
          <h3>No objectives match these filters</h3>
          <p>
            <button type="button" className="btn-link" onClick={() => onFilters?.(NO_FILTERS)}>Clear filters</button>
            {' '}to see all {items.length}.
          </p>
        </div>
      ) : (
        <div className="ismsm-grid">
          {shown.tiles.map((o) => (
            <MetricTile key={o.id} objective={o} />
          ))}
        </div>
      )}

      {adding && <ObjectiveFormModal organizationId={organizationId} onClose={() => setAdding(false)} />}
    </div>
  )
}
