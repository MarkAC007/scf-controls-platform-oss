// SPDX-License-Identifier: AGPL-3.0-only
/**
 * IsmsSummaryPanel — the dashboard's summary of the ISMS metrics: the same
 * five figures as the head of Analytics › ISMS metrics, each a link to the
 * objectives it counts. The tiles, trends and filters stay on that screen.
 */
import type { JSX } from 'react'
import { useIsmsMetrics } from '../../hooks/useSecurityObjectives'
import AppLink from '../AppLink'
import ObjectiveSummaryStrip from '../analytics/ObjectiveSummaryStrip'
import { errorText, formatDateTime } from '../isms/objectives/objectiveText'

export default function IsmsSummaryPanel({ organizationId }: { organizationId: string }): JSX.Element {
  const metrics = useIsmsMetrics(organizationId)
  const data = metrics.data

  return (
    <section className="dashboard-section" aria-labelledby="dashboard-isms">
      <div className="dashboard-section-head">
        <h2 id="dashboard-isms" className="dashboard-section-title">ISMS metrics</h2>
        {data && <span className="dashboard-section-meta">Measured {formatDateTime(data.measured_at)}</span>}
        <AppLink to={{ kind: 'isms-metrics' }} className="dashboard-section-link">
          Open ISMS metrics →
        </AppLink>
      </div>
      {data ? (
        data.summary.objectives > 0 ? (
          <ObjectiveSummaryStrip summary={data.summary} linked />
        ) : (
          <p className="dashboard-card dashboard-card-empty">
            No security objectives yet. Add the first one on ISMS metrics.
          </p>
        )
      ) : metrics.isError ? (
        <p className="dashboard-card dashboard-card-empty" role="alert">
          Could not load the ISMS metrics. {errorText(metrics.error)}
        </p>
      ) : (
        <p className="dashboard-card dashboard-card-empty">Loading the ISMS metrics…</p>
      )}
    </section>
  )
}
