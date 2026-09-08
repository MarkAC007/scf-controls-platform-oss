/**
 * Focus refetch is a single global default, and stays that way (epic #921).
 *
 * The stale-UI problem was never one missing button. It was that nothing in
 * the client re-read the server when a user came back to the tab, because the
 * global default said the option was off and nine hooks repeated that answer
 * locally. Flipping the default is only half the fix; the sweep below is the
 * other half, and it is the part that survives the next person who reaches
 * for a local override out of habit.
 *
 * The sweep reads the sources through Vite's `import.meta.glob` rather than
 * node's fs, so it type-checks under the app's own tsconfig (which has no
 * node types) and runs the same way the app is built.
 */
import { describe, expect, it } from 'vitest'

import { queryClient } from '../queryClient'

const SOURCES = import.meta.glob('../../**/*.{ts,tsx}', {
  query: '?raw',
  import: 'default',
  eager: true,
}) as Record<string, string>

const OPTION = 'refetchOnWindowFocus'

// Test files are excluded: a test may legitimately construct a query with the
// option off, and this file names the string in its own prose.
const APP_SOURCES = Object.entries(SOURCES).filter(([path]) => !path.includes('__tests__'))

describe('window-focus refetching', () => {
  it('is on by default for every query', () => {
    expect(queryClient.getDefaultOptions().queries?.refetchOnWindowFocus).toBe(true)
  })

  it('is not switched off again by any hook or component', () => {
    const offenders = APP_SOURCES.flatMap(([path, source]) =>
      source
        .split('\n')
        .map((line, index) => ({ line: line.trim(), number: index + 1 }))
        .filter(({ line }) => new RegExp(`${OPTION}:\\s*false`).test(line))
        .map(({ number }) => `${path}:${number}`)
    )

    expect(offenders).toEqual([])
  })

  it('the sweep can actually see the sources it claims to check', () => {
    // Guards the assertion above: a glob that matched nothing, or a regex that
    // matches nothing, would pass that test silently forever.
    expect(APP_SOURCES.length).toBeGreaterThan(100)
    expect(APP_SOURCES.some(([, source]) => source.includes(OPTION))).toBe(true)
  })
})
