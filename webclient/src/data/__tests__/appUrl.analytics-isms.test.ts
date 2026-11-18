// SPDX-License-Identifier: AGPL-3.0-only
/**
 * The Analytics tab's `view` (ISMS metrics or Capability posture) and the ISMS
 * profile's `section` and `objective`. Each is read only on its own tab and
 * leaves with it, so `view` never collides with the evidence or deficiencies one.
 */
import { describe, expect, it } from 'vitest'

import {
  readAppLocation,
  withAnalyticsView,
  withIsmsObjective,
  withIsmsSection,
  withTab,
} from '../appUrl'

const get = (search: string, key: string) => new URLSearchParams(search).get(key)

describe('appUrl: the Analytics view', () => {
  it('reads the view on the Analytics tab', () => {
    expect(readAppLocation('?tab=capability-posture&view=capability-posture').analyticsView).toBe('capability-posture')
    expect(readAppLocation('?tab=capability-posture&view=isms-metrics').analyticsView).toBe('isms-metrics')
  })

  it('defaults to ISMS metrics when the URL names none, or one it does not know', () => {
    expect(readAppLocation('?tab=capability-posture').analyticsView).toBe('isms-metrics')
    expect(readAppLocation('?tab=capability-posture&view=board').analyticsView).toBe('isms-metrics')
  })

  it('does not read the view on another tab, and lends its own to none', () => {
    expect(readAppLocation('?tab=evidence&view=capability-posture').analyticsView).toBe('isms-metrics')
    expect(readAppLocation('?tab=deficiencies&view=capability-posture').analyticsView).toBe('isms-metrics')
    expect(readAppLocation('?tab=capability-posture&view=capability-posture').evidenceView).toBe('dashboard')
    expect(readAppLocation('?tab=capability-posture&view=capability-posture').deficiencyView).toBeNull()
  })

  it('writes the view, keeping unrelated parameters', () => {
    const next = withAnalyticsView('tab=capability-posture&invite_type=x', 'capability-posture')
    expect(get(next, 'view')).toBe('capability-posture')
    expect(get(next, 'tab')).toBe('capability-posture')
    expect(get(next, 'invite_type')).toBe('x')
  })

  it('lands on the Analytics tab from another, dropping that tab’s own parameters', () => {
    const next = withAnalyticsView('tab=evidence&view=workspace&item=E-1', 'isms-metrics')
    expect(get(next, 'tab')).toBe('capability-posture')
    expect(get(next, 'view')).toBe('isms-metrics')
    expect(get(next, 'item')).toBeNull()
  })

  it('leaves with the tab', () => {
    expect(get(withTab('tab=capability-posture&view=capability-posture', 'scoping'), 'view')).toBeNull()
  })
})

describe('appUrl: the ISMS section and objective', () => {
  it('reads section and objective only on the ISMS tab', () => {
    const loc = readAppLocation('?tab=isms&section=objectives&objective=obj-1')
    expect(loc.ismsSection).toBe('objectives')
    expect(loc.ismsObjective).toBe('obj-1')
    const other = readAppLocation('?tab=scoping&section=objectives&objective=obj-1')
    expect(other.ismsSection).toBeNull()
    expect(other.ismsObjective).toBeNull()
  })

  it('reads an unknown section as none', () => {
    expect(readAppLocation('?tab=isms&section=nowhere').ismsSection).toBeNull()
  })

  it('opens Objectives for an objective with no section', () => {
    const loc = readAppLocation('?tab=isms&objective=obj-1')
    expect(loc.ismsSection).toBe('objectives')
    expect(loc.ismsObjective).toBe('obj-1')
  })

  it('ignores an objective under another section', () => {
    const loc = readAppLocation('?tab=isms&section=scope&objective=obj-1')
    expect(loc.ismsSection).toBe('scope')
    expect(loc.ismsObjective).toBeNull()
  })

  it('drops the objective when the section is not Objectives', () => {
    const next = withIsmsSection('tab=isms&section=objectives&objective=obj-1', 'scope')
    expect(get(next, 'section')).toBe('scope')
    expect(get(next, 'objective')).toBeNull()
    const kept = withIsmsSection('tab=isms&section=objectives&objective=obj-1', 'objectives')
    expect(get(kept, 'objective')).toBe('obj-1')
  })

  it('builds a full objective link from nothing', () => {
    expect(withIsmsObjective('', 'x')).toBe('tab=isms&section=objectives&objective=x')
    expect(withIsmsObjective('tab=isms&section=objectives&objective=x', null)).toBe('tab=isms&section=objectives')
  })

  it('leaves with the tab', () => {
    const next = withTab('tab=isms&section=objectives&objective=obj-1', 'scoping')
    expect(get(next, 'section')).toBeNull()
    expect(get(next, 'objective')).toBeNull()
  })
})
