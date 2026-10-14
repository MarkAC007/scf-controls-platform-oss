/**
 * The deficiencies tab's `view` (#1155): List or Board.
 *
 * `view` is also the evidence workspace's parameter name. Each is read only
 * on its own tab and leaves with it, so the two never collide.
 */
import { describe, expect, it } from 'vitest'

import {
  PARAM_DEFICIENCY_VIEW,
  readAppLocation,
  withDeficiencyView,
  withTab,
} from '../appUrl'

const get = (search: string, key: string) => new URLSearchParams(search).get(key)

describe('appUrl: the deficiencies view (#1155)', () => {
  it('reads the view on the deficiencies tab', () => {
    expect(readAppLocation('?tab=deficiencies&view=board').deficiencyView).toBe('board')
    expect(readAppLocation('?tab=deficiencies&view=list').deficiencyView).toBe('list')
  })

  it('reads no view when the URL names none, or names one it does not know', () => {
    expect(readAppLocation('?tab=deficiencies').deficiencyView).toBeNull()
    expect(readAppLocation('?tab=deficiencies&view=workspace').deficiencyView).toBeNull()
  })

  it('ignores the evidence workspace view, and lends its own to no other tab', () => {
    expect(readAppLocation('?tab=evidence&view=board').deficiencyView).toBeNull()
    expect(readAppLocation('?view=board').deficiencyView).toBeNull()
    // And the evidence screen does not read "board" as one of its own.
    expect(readAppLocation('?tab=deficiencies&view=board').evidenceView).toBe('dashboard')
  })

  it('writes the view on the deficiencies tab, keeping unrelated parameters', () => {
    const next = withDeficiencyView('?tab=deficiencies&invite_type=x', 'board')
    expect(get(next, PARAM_DEFICIENCY_VIEW)).toBe('board')
    expect(get(next, 'tab')).toBe('deficiencies')
    expect(get(next, 'invite_type')).toBe('x')
    expect(get(withDeficiencyView(next, 'list'), PARAM_DEFICIENCY_VIEW)).toBe('list')
  })

  it('lands on the deficiencies tab when written from another, dropping that tab’s own view', () => {
    const next = withDeficiencyView('?tab=evidence&view=workspace&item=E-1', 'board')
    expect(get(next, 'tab')).toBe('deficiencies')
    expect(get(next, PARAM_DEFICIENCY_VIEW)).toBe('board')
    expect(get(next, 'item')).toBeNull()
  })

  it('takes the view with it when leaving the deficiencies tab', () => {
    const next = withTab('?tab=deficiencies&view=board', 'evidence')
    expect(get(next, 'view')).toBeNull()
    expect(readAppLocation(`?${next}`).evidenceView).toBe('dashboard')
  })
})
