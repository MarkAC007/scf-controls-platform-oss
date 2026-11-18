// SPDX-License-Identifier: AGPL-3.0-only
/**
 * The ISMS metric detail (`metric`) and the dashboard filters (`team`,
 * `clause`, `status`) are owned by the Analytics tab: read only on its ISMS
 * metrics view, and dropped when the tab is left.
 */
import { describe, expect, it } from 'vitest'

import {
  readAppLocation,
  withAnalyticsView,
  withIsmsFilters,
  withIsmsMetric,
  withTab,
} from '../appUrl'

const get = (search: string, key: string) => new URLSearchParams(search).get(key)

const UUID = '9b2f0c1e-4a7d-4e2b-8c3f-1d2e3f4a5b6c'

describe('appUrl: the ISMS metric detail', () => {
  it('reads the metric on the ISMS metrics view only', () => {
    expect(readAppLocation(`?tab=capability-posture&view=isms-metrics&metric=${UUID}`).ismsMetric).toBe(UUID)
    // ISMS metrics is the default view, so a URL without `view` still opens it.
    expect(readAppLocation(`?tab=capability-posture&metric=${UUID}`).ismsMetric).toBe(UUID)
    expect(readAppLocation(`?tab=capability-posture&view=capability-posture&metric=${UUID}`).ismsMetric).toBeNull()
    expect(readAppLocation(`?tab=evidence&metric=${UUID}`).ismsMetric).toBeNull()
  })

  it('reads a metric that is not a UUID as no metric, so it never reaches an API path (review M1)', () => {
    const read = (v: string) =>
      readAppLocation(`?tab=capability-posture&view=isms-metrics&metric=${encodeURIComponent(v)}`).ismsMetric
    expect(read('../../x')).toBeNull()
    expect(read('abc')).toBeNull()
    expect(read(`${UUID}/../../admin`)).toBeNull()
    expect(read(`${UUID}x`)).toBeNull()
    expect(read(`x${UUID}`)).toBeNull()
    expect(read('9b2f0c1e-4a7d-4e2b-8c3f-1d2e3f4a5b6')).toBeNull()
    // Either case is a UUID.
    expect(read(UUID.toUpperCase())).toBe(UUID.toUpperCase())
  })

  it('opens a metric from any tab, dropping that tab’s own parameters', () => {
    const next = withIsmsMetric('tab=evidence&view=workspace&item=E-1&invite_type=x', 'obj-1')
    expect(get(next, 'tab')).toBe('capability-posture')
    expect(get(next, 'view')).toBe('isms-metrics')
    expect(get(next, 'metric')).toBe('obj-1')
    expect(get(next, 'item')).toBeNull()
    expect(get(next, 'invite_type')).toBe('x')
  })

  it('closes the metric with null, and when the view is named again, keeping the filters', () => {
    const open = 'tab=capability-posture&view=isms-metrics&metric=obj-1&team=t1'
    expect(get(withIsmsMetric(open, null), 'metric')).toBeNull()
    expect(get(withIsmsMetric(open, null), 'team')).toBe('t1')
    expect(get(withAnalyticsView(open, 'isms-metrics'), 'metric')).toBeNull()
  })

  it('leaves with the tab', () => {
    const next = withTab('tab=capability-posture&view=isms-metrics&metric=obj-1&team=t1&clause=6&status=off_track', 'risk-register')
    for (const key of ['metric', 'team', 'clause', 'status', 'view']) expect(get(next, key)).toBeNull()
  })
})

describe('appUrl: the ISMS metrics filters', () => {
  it('reads the three filters on the ISMS metrics view, blank elsewhere', () => {
    expect(readAppLocation('?tab=capability-posture&team=t1&clause=annex-a&status=off_track').ismsFilters)
      .toEqual({ team: 't1', clause: 'annex-a', status: 'off_track' })
    expect(readAppLocation('?tab=capability-posture&view=capability-posture&team=t1').ismsFilters)
      .toEqual({ team: '', clause: '', status: '' })
    expect(readAppLocation('?tab=deficiencies&status=open').ismsFilters).toEqual({ team: '', clause: '', status: '' })
  })

  it('writes set filters and removes blank ones', () => {
    const next = withIsmsFilters('tab=capability-posture&view=isms-metrics&status=at_risk', { team: 't1', clause: '6', status: '' })
    expect(get(next, 'team')).toBe('t1')
    expect(get(next, 'clause')).toBe('6')
    expect(get(next, 'status')).toBeNull()
    expect(get(next, 'view')).toBe('isms-metrics')
  })

  it('drops the filters when Capability posture is chosen', () => {
    const next = withAnalyticsView('tab=capability-posture&view=isms-metrics&team=t1&clause=6&status=off_track', 'capability-posture')
    for (const key of ['team', 'clause', 'status']) expect(get(next, key)).toBeNull()
  })
})
