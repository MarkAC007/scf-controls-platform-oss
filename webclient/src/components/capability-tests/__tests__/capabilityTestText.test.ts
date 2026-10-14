import { afterEach, describe, expect, it, vi } from 'vitest'

import { latestTestDay } from '../../../hooks/useCapabilityTests'
import { RESULTS, TESTED_ON_FUTURE, resultProblem } from '../capabilityTestText'

/** Sets the browser's zone and clock. Node re-reads TZ when it is assigned; the check proves Date follows it. */
function clockAt(zone: string, utc: string) {
  vi.stubEnv('TZ', zone)
  vi.useFakeTimers({ toFake: ['Date'] })
  vi.setSystemTime(new Date(utc))
  const hour = new Intl.DateTimeFormat('en-US', { timeZone: zone, hour: 'numeric', hourCycle: 'h23' }).format(new Date())
  expect(new Date().getHours()).toBe(Number(hour))
}

describe('resultProblem: the server\'s rule, in its words (services/capability_tests.py)', () => {
  it('lets each result stand with the findings it needs', () => {
    expect(resultProblem('passed', 0)).toBeNull()
    expect(resultProblem('passed_with_findings', 1)).toBeNull()
    expect(resultProblem('failed', 3)).toBeNull()
  })

  it('refuses a pass with findings', () => {
    expect(resultProblem('passed', 2)).toBe(
      'A test with 2 finding(s) did not simply pass: record it as passed_with_findings or failed',
    )
  })

  it('refuses a failure or a pass with findings that has none', () => {
    expect(resultProblem('failed', 0)).toBe('A test that failed needs at least one finding')
    expect(resultProblem('passed_with_findings', 0)).toBe('A test that passed with findings needs at least one finding')
  })

  it('knows the three results the server takes, in its order', () => {
    expect(RESULTS).toEqual(['passed', 'passed_with_findings', 'failed'])
  })

  it('words the future-date refusal as the server does', () => {
    expect(TESTED_ON_FUTURE).toBe('tested_on cannot be in the future: record a test once it has run')
  })
})

describe('latestTestDay: never after the server\'s UTC day', () => {
  afterEach(() => {
    vi.useRealTimers()
    vi.unstubAllEnvs()
  })

  it('is the server\'s day while the reader east of Greenwich is already on the next', () => {
    clockAt('Europe/London', '2026-10-13T23:30:00Z') // 00:30 BST on the 14th
    expect(latestTestDay()).toBe('2026-10-13')
  })

  it('is the reader\'s day once the server\'s has caught up', () => {
    clockAt('Europe/London', '2026-10-14T00:30:00Z')
    expect(latestTestDay()).toBe('2026-10-14')
  })

  it('is the reader\'s day west of Greenwich, where the server is already a day ahead', () => {
    clockAt('America/New_York', '2026-10-14T02:00:00Z') // 22:00 EDT on the 13th
    expect(latestTestDay()).toBe('2026-10-13')
  })
})
