/**
 * How audit-log rows read: field labels, entity type names and value
 * formatting, shared by the org-wide page and the per-control panel so the two
 * cannot drift apart.
 */
import { DEFICIENCY_STATUS_LABELS, RESULT_LABELS, TEST_TYPE_LABELS } from '../data/domainLabels'
import { userLabel } from '../data/userDisplay'
import type { UserSimple } from '../types'
import { decodeAuditValue, formatAuditDate, formatServerDay, isDateField } from './auditValue'

/** Human-readable labels for tracked fields. Field names are not namespaced, so a label must hold for every entity that writes it. */
const FIELD_LABELS: Record<string, string> = {
  selected: 'Scoped',
  implementation_status: 'Status',
  priority: 'Priority',
  owner: 'Owner',
  assigned_to: 'Assigned To',
  maturity_level: 'Maturity Level',
  selection_reason: 'Selection Reason',
  target_date: 'Target Date',
  completion_date: 'Completion Date',
  implementation_notes: 'Notes',
  control_weighting: 'Control Weighting',
  validation_cadence: 'Validation Cadence',
  nist_csf_function: 'NIST CSF Function',
  control_question: 'Control Question',
  related_documentation: 'Documentation',
  custom_fields: 'Custom Fields',
  // Scoped control: scoping, obligation basis, targets, materiality and
  // validation (PIG R2, R5, R6, R10).
  out_of_scope_justification: 'Out-of-Scope Justification',
  scope_override: 'Scope Override',
  scope_override_reason: 'Scope Override Reason',
  scope_override_set_at: 'Scope Override Set',
  scope_override_set_by: 'Scope Override Set By',
  scope_override_approved_by: 'Exclusion Approved By',
  basis_override: 'Obligation Basis Override',
  basis_override_reason: 'Basis Override Reason',
  target_maturity_override: 'Target Maturity Override',
  target_maturity_reason: 'Target Maturity Reason',
  material_override: 'Materiality Override',
  material_reason: 'Materiality Reason',
  procedure_document_id: 'Procedure Document',
  last_validated_at: 'Last Validated',
  last_validated_by: 'Last Validated By',
  last_validation_note: 'Validation Note',
  // Framework selection basis (PIG R2).
  basis: 'Obligation Basis',
  obligation_type: 'Obligation Type',
  obligation_source: 'Obligation Source',
  // System and vendor attributes (PIG R4).
  in_scope: 'In Scope',
  criticality: 'Criticality',
  rto_hours: 'RTO (hours)',
  rpo_hours: 'RPO (hours)',
  data_classification: 'Data Classification',
  hosting_location: 'Hosting Location',
  data_location: 'Data Location',
  flow_down_requirements: 'Flow-down Requirements',
  shared_responsibility_note: 'Shared Responsibility',
  // Risk acceptance (PIG R7) and a custom risk's supporting document.
  owner_user_id: 'Owner',
  accepted_by: 'Accepted By',
  accepted_at: 'Accepted',
  acceptance_rationale: 'Acceptance Rationale',
  acceptance_expires_on: 'Acceptance Expires',
  document_id: 'Supporting Document',
  // Deficiencies (PIG R8, R11).
  title: 'Title',
  description: 'Description',
  status: 'Status',
  owner_name: 'Owner Name',
  due_date: 'Due Date',
  completed_date: 'Completed Date',
  scoped_control_id: 'Control Record',
  scf_id: 'Control',
  risk_code: 'Risk',
  source: 'Source',
  source_ref: 'Source Reference',
  retest_note: 'Retest Note',
  closed_by_user_id: 'Closed By',
  capability_test_id: 'Capability Test',
  root_cause: 'Root Cause',
  // Compensating controls (PIG R9).
  gap_description: 'Gap',
  compensating_control: 'Compensating Control',
  effectiveness_rating: 'Effectiveness',
  risk_reduction_notes: 'Risk Reduction Notes',
  // Capability tests (PIG R11).
  test_type: 'Test Type',
  tested_on: 'Tested On',
  scenario: 'Scenario',
  result: 'Result',
  outcome: 'Outcome',
  recorded_by_user_id: 'Recorded By',
  // Posture snapshots (PIG R10).
  period: 'Period',
  counts: 'Counts',
  trigger: 'Trigger',
  taken_by_user_id: 'Taken By',
  // Journey stages.
  state: 'State',
  started_at: 'Started',
  attested_by_user_id: 'Attested By',
  attested_at: 'Attested',
  attestation_note: 'Attestation Note',
  precondition_spec: 'Preconditions',
}

export function auditFieldLabel(field: string): string {
  return FIELD_LABELS[field] || field
}

/**
 * The entity types the audit log can be filtered by, in the filter's order.
 * The filter labels each with `auditEntityTypeLabel`, as the rows do, so an
 * option and the rows it selects cannot read differently.
 */
export const AUDIT_ENTITY_TYPES: readonly string[] = [
  'scoped_control',
  'evidence',
  'risk',
  'vendor',
  'system',
  'user',
  'organization',
  'webhook',
  'deficiency',
  'capability_test',
  'compensating_control',
  'posture_snapshot',
  'journey_stage',
  'journey',
  'organization_framework_selection',
]

/** Entity types whose stored name does not read as a phrase. Every other type is its name in sentence case. */
const ENTITY_TYPE_LABELS: Record<string, string> = {
  organization_framework_selection: 'Framework selection',
}

/** A snake_case code as a phrase: "journey_stage" reads "Journey stage". */
const sentenceCase = (value: string) => {
  const words = value.replace(/_/g, ' ')
  return words.charAt(0).toUpperCase() + words.slice(1)
}

export function auditEntityTypeLabel(type: string): string {
  return ENTITY_TYPE_LABELS[type] ?? sentenceCase(type)
}

/** Fields that store a snake_case enum value. */
const ENUM_FIELDS = new Set([
  'implementation_status',
  'status',
  'result',
  'test_type',
  'source',
  'effectiveness_rating',
  'trigger',
  'treatment_status',
  'criticality',
  'data_classification',
  'scope_override',
  'obligation_type',
  'state',
])

/**
 * Enum fields whose values the product already labels, by the entity whose
 * field it is: `status` is written by engagements, queries and grants as well
 * as deficiencies, and only a deficiency's reads through the deficiency map.
 * Every other enum value reads in sentence case, as these labels do, so one
 * column keeps one casing.
 */
const DOMAIN_ENUM_LABELS: Record<string, Record<string, Record<string, string>>> = {
  capability_test: { result: RESULT_LABELS, test_type: TEST_TYPE_LABELS },
  deficiency: { status: DEFICIENCY_STATUS_LABELS },
}

/** Fields that store a user id, beyond the `*_by_user_id` ones. */
const USER_ID_FIELDS = new Set([
  'last_validated_by',
  'scope_override_set_by',
  'scope_override_approved_by',
  'accepted_by',
  'owner_user_id',
])

const isUserIdField = (field: string) => USER_ID_FIELDS.has(field) || field.endsWith('_by_user_id')

const UUID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i

/** How a user id that is not among the organisation's members reads, before its short id. */
export const UNKNOWN_MEMBER = 'Unknown member'

/** Obligation basis codes, which read as their abbreviation. */
const BASIS_FIELDS = new Set(['basis', 'basis_override'])

/** Fields that store a JSON boolean. */
const BOOLEAN_FIELDS = new Set(['selected', 'in_scope', 'material_override'])

/** Server timestamps, shown as the day the server recorded them. */
const TIMESTAMP_FIELDS = new Set([
  'last_validated_at',
  'accepted_at',
  'scope_override_set_at',
  'started_at',
  'attested_at',
])

/** Date-only fields whose name ends in `_on`, which `isDateField` does not match. */
const ON_DATE_FIELDS = new Set(['tested_on', 'acceptance_expires_on'])

const titleCase = (value: string) => value.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase())

export interface AuditFieldFormat {
  /** Longest result, ellipsis included. Default 80. */
  maxLength?: number
  /** The audited entity's type, which decides which domain labels apply. */
  entityType?: string
  /** The organisation's members, to name user ids. Omit when they could not be read: ids then show as stored. */
  members?: readonly UserSimple[]
  /** True while the members load: user ids read as loading rather than flash up. */
  membersLoading?: boolean
}

/**
 * Format a stored audit value for display.
 *
 * A user id in a field that stores one reads as that member's name, or as
 * `Unknown member (abcd1234…)` when no member has it, so a departed user stays
 * identifiable. Without a member list (it failed to load) the id is shown.
 */
export function formatAuditField(field: string, raw: string | undefined, format: AuditFieldFormat = {}): string {
  const { maxLength = 80, entityType, members, membersLoading = false } = format
  const value = decodeAuditValue(raw)
  if (value === null) return '—'
  if (isUserIdField(field) && UUID_PATTERN.test(value)) {
    if (membersLoading) return 'Loading…'
    if (members) {
      const member = members.find((m) => m.id.toLowerCase() === value.toLowerCase())
      return member ? userLabel(member) : `${UNKNOWN_MEMBER} (${value.slice(0, 8)}…)`
    }
  }
  if (BOOLEAN_FIELDS.has(field)) return value.toLowerCase() === 'true' ? 'Yes' : 'No'
  if (ENUM_FIELDS.has(field)) {
    return (entityType && DOMAIN_ENUM_LABELS[entityType]?.[field]?.[value]) || sentenceCase(value)
  }
  if (BASIS_FIELDS.has(field)) return value.length <= 3 ? value.toUpperCase() : titleCase(value)
  if (field === 'priority') return value.charAt(0).toUpperCase() + value.slice(1)
  if (field === 'maturity_level') return value.toUpperCase()
  if (TIMESTAMP_FIELDS.has(field)) return formatServerDay(value)
  if (isDateField(field) || ON_DATE_FIELDS.has(field)) return formatAuditDate(value)
  if (field === 'period' && /^\d{4}-\d{2}/.test(value)) {
    const month = new Date(`${value.slice(0, 7)}-01T00:00:00`)
    if (!Number.isNaN(month.getTime())) {
      return month.toLocaleDateString('en-US', { year: 'numeric', month: 'short' })
    }
  }
  return value.length > maxLength ? value.substring(0, maxLength - 3) + '...' : value
}
