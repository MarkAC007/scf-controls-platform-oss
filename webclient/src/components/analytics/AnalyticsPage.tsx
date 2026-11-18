// SPDX-License-Identifier: AGPL-3.0-only
/**
 * AnalyticsPage — the Analytics tab: ISMS metrics (first, the default) and
 * Capability posture (today's screen, unchanged), as two sub-tabs, plus "All
 * organisations" for someone who can open two or more (phase 3 B3).
 *
 * Owns `view` on this tab, as EvidenceWorkspace owns it on the evidence tab;
 * App still owns `tab`. Seeded from the URL, written with `replaceState` (the
 * sub-tabs are halves of one screen, not places), re-read on Back and Forward.
 * Nothing is written on arrival.
 *
 * Also owns, on ISMS metrics, `metric` (an objective's detail screen: a place,
 * so opening it pushes a history entry, through AppLink) and the dashboard
 * filters `team`, `clause` and `status` (written with `replaceState`).
 */
import { useEffect, useState, type JSX } from 'react'
import {
  readAppLocation,
  replaceSearch,
  withAnalyticsView,
  withIsmsFilters,
  type AnalyticsView,
  type IsmsMetricFilters,
} from '../../data/appUrl'
import CapabilityPosture from '../CapabilityPosture'
import TabRow, { type TabRowItem } from '../explorer/TabRow'
import AllOrganisationsTable from './AllOrganisationsTable'
import IsmsMetricsDashboard from './IsmsMetricsDashboard'
import MetricDetail from './MetricDetail'

const ANALYTICS_TABS: TabRowItem[] = [
  { id: 'isms-metrics', label: 'ISMS metrics' },
  { id: 'capability-posture', label: 'Capability posture' },
]
const ALL_ORGANISATIONS_TAB: TabRowItem = { id: 'all-organisations', label: 'All organisations' }

interface Props {
  organizationId: string
  /** Every organisation the person can open (OrganizationContext's `availableOrgs`). */
  organizations?: { id: string; name: string }[]
  /** OrganizationContext's `switchOrganization`. */
  onSwitchOrganization?: (organizationId: string) => Promise<void>
}

const NO_ORGANIZATIONS: { id: string; name: string }[] = []

export default function AnalyticsPage({
  organizationId,
  organizations = NO_ORGANIZATIONS,
  onSwitchOrganization,
}: Props): JSX.Element {
  const [requestedView, setView] = useState<AnalyticsView>(() => readAppLocation(window.location.search).analyticsView)
  const manyOrganisations = organizations.length > 1
  // With one organisation the sub-tab does not exist, whatever the URL says.
  const view: AnalyticsView = requestedView === 'all-organisations' && !manyOrganisations ? 'isms-metrics' : requestedView
  const tabs = manyOrganisations ? [...ANALYTICS_TABS, ALL_ORGANISATIONS_TAB] : ANALYTICS_TABS
  const [metric, setMetric] = useState<string | null>(() => readAppLocation(window.location.search).ismsMetric)
  const [filters, setFilters] = useState<IsmsMetricFilters>(() => readAppLocation(window.location.search).ismsFilters)

  const select = (id: string) => {
    const next: AnalyticsView =
      id === 'capability-posture' || id === 'all-organisations' ? id : 'isms-metrics'
    setView(next)
    setMetric(null)
    if (next !== 'isms-metrics') setFilters({ team: '', clause: '', status: '' })
    replaceSearch(withAnalyticsView(window.location.search, next))
  }

  // "Open" on a row: the URL is written first, because switching organisation reloads the
  // screens under App and this page may mount again from the URL before the switch resolves.
  const openOrganisation = async (id: string) => {
    const back = window.location.search
    select('isms-metrics')
    if (id === organizationId || !onSwitchOrganization) return
    try {
      await onSwitchOrganization(id)
    } catch {
      replaceSearch(back.replace(/^\?/, ''))
      setView('all-organisations')
    }
  }

  const changeFilters = (next: IsmsMetricFilters) => {
    setFilters(next)
    replaceSearch(withIsmsFilters(window.location.search, next))
  }

  useEffect(() => {
    const onPopState = () => {
      const loc = readAppLocation(window.location.search)
      if (loc.tab === 'capability-posture') {
        setView(loc.analyticsView)
        setMetric(loc.ismsMetric)
        setFilters(loc.ismsFilters)
      }
    }
    window.addEventListener('popstate', onPopState)
    return () => window.removeEventListener('popstate', onPopState)
  }, [])

  return (
    <div className="analytics-page">
      <TabRow tabs={tabs} activeId={view} onSelect={select} aria-label="Analytics sub-tabs" />
      {view === 'all-organisations' ? (
        <AllOrganisationsTable currentOrganizationId={organizationId} onOpen={(id) => void openOrganisation(id)} />
      ) : view === 'isms-metrics' && metric ? (
        <MetricDetail key={metric} organizationId={organizationId} objectiveId={metric} />
      ) : view === 'isms-metrics' ? (
        <IsmsMetricsDashboard organizationId={organizationId} filters={filters} onFilters={changeFilters} />
      ) : (
        <CapabilityPosture organizationId={organizationId} />
      )}
    </div>
  )
}
