import type { CapabilityTestResult, CapabilityTestType } from '../../types'

export { RESULT_LABELS, TEST_TYPE_LABELS } from '../../data/domainLabels'

export const TEST_TYPES: CapabilityTestType[] = ['incident_response', 'continuity', 'other']

export const RESULTS: CapabilityTestResult[] = ['passed', 'passed_with_findings', 'failed']

/**
 * Why `result` cannot stand with this many findings, or null: the server's
 * rule and words (`result_problem` in services/capability_tests.py). Passed
 * means nothing was found; the other two each need at least one finding.
 */
export function resultProblem(result: CapabilityTestResult, findingCount: number): string | null {
  if (result === 'passed' && findingCount) {
    return (
      `A test with ${findingCount} finding(s) did not simply pass: ` +
      'record it as passed_with_findings or failed'
    )
  }
  if (result !== 'passed' && !findingCount) {
    return `A test that ${result === 'failed' ? 'failed' : 'passed with findings'} needs at least one finding`
  }
  return null
}

/** The server's refusal of a test dated after its day (`tested_on_problem`). */
export const TESTED_ON_FUTURE = 'tested_on cannot be in the future: record a test once it has run'

/** Each finding is recorded as a deficiency, and a test is never deleted. */
export const RECORD_WARNING =
  'A test cannot be deleted, only corrected. Each finding becomes a deficiency on the Deficiencies (POA&M) page, ' +
  'where it is owned, closed or, if found in error, cancelled.'
