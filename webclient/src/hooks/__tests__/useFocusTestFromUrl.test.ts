import { act, renderHook } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'

import { readAppLocation } from '../../data/appUrl'
import { useFocusTestFromUrl } from '../useCapabilityTests'

// App's reader, defined once as App defines it.
const readItem = (search: string) => readAppLocation(search).capabilityTestItem

function goTo(search: string) {
  window.history.pushState(null, '', `/${search}`)
}
function popTo(search: string) {
  act(() => {
    window.history.replaceState(null, '', `/${search}`)
    window.dispatchEvent(new PopStateEvent('popstate'))
  })
}

describe("useFocusTestFromUrl: App's ?test= (PIG R11)", () => {
  afterEach(() => window.history.replaceState(null, '', '/'))

  it('opens the test a link arrives with', () => {
    goTo('?tab=capability-tests&test=ct-1')
    const { result } = renderHook(() => useFocusTestFromUrl('capability-tests', readItem))
    expect(result.current).toBe('ct-1')
  })

  it('follows Back and Forward to the test each history entry names', () => {
    goTo('?tab=capability-tests&test=ct-1')
    const { result } = renderHook(() => useFocusTestFromUrl('capability-tests', readItem))
    popTo('?tab=capability-tests&test=ct-2')
    expect(result.current).toBe('ct-2')
    popTo('?tab=capability-tests')
    expect(result.current).toBeNull()
    popTo('?tab=capability-tests&test=ct-1')
    expect(result.current).toBe('ct-1')
    // Another tab's entry names no test, whatever its params.
    popTo('?tab=deficiencies&test=ct-1')
    expect(result.current).toBeNull()
  })

  it('drops the test on leaving the tab, so returning through the sidebar does not reopen it', () => {
    goTo('?tab=capability-tests&test=ct-1')
    const { result, rerender } = renderHook(({ tab }) => useFocusTestFromUrl(tab, readItem), {
      initialProps: { tab: 'capability-tests' },
    })
    expect(result.current).toBe('ct-1')
    rerender({ tab: 'deficiencies' })
    expect(result.current).toBeNull()
    rerender({ tab: 'capability-tests' })
    expect(result.current).toBeNull()
  })
})
