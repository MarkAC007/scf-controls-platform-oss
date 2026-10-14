/**
 * How the product names the stored codes of its domain enums. Pure data, so
 * that both the pages that edit these records and the audit log that reports
 * changes to them (utils/auditLabels) read them from one place.
 */
import type { ActionItemStatus, CapabilityTestResult, CapabilityTestType } from '../types'

/** A capability test's type (PIG R11). */
export const TEST_TYPE_LABELS: Record<CapabilityTestType, string> = {
  incident_response: 'Incident response',
  continuity: 'Continuity',
  other: 'Other',
}

/** A capability test's result (PIG R11). */
export const RESULT_LABELS: Record<CapabilityTestResult, string> = {
  passed: 'Passed',
  passed_with_findings: 'Passed with findings',
  failed: 'Failed',
}

/** A deficiency's status (PIG R8). */
export const DEFICIENCY_STATUS_LABELS: Record<ActionItemStatus, string> = {
  open: 'Open',
  in_progress: 'In progress',
  completed: 'Completed',
  cancelled: 'Cancelled',
}
