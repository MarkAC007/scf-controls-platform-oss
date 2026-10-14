/**
 * Labels for what the PIG work writes to the audit log: new entity types, and
 * fields that printed as snake_case (last_validated_by, root_cause) with values
 * that printed as stored (passed_with_findings, "2026-10-13 12:47:12.210318").
 */
import { describe, expect, it } from 'vitest'

import type { UserSimple } from '../../types'
import {
  AUDIT_ENTITY_TYPES,
  UNKNOWN_MEMBER,
  auditEntityTypeLabel,
  auditFieldLabel,
  formatAuditField,
} from '../auditLabels'

describe('auditFieldLabel', () => {
  it.each([
    ['last_validated_at', 'Last Validated'],
    ['last_validated_by', 'Last Validated By'],
    ['last_validation_note', 'Validation Note'],
    ['material_override', 'Materiality Override'],
    ['target_maturity_override', 'Target Maturity Override'],
    ['basis_override', 'Obligation Basis Override'],
    ['root_cause', 'Root Cause'],
    ['retest_note', 'Retest Note'],
    ['capability_test_id', 'Capability Test'],
    ['effectiveness_rating', 'Effectiveness'],
    ['tested_on', 'Tested On'],
    ['recorded_by_user_id', 'Recorded By'],
    ['taken_by_user_id', 'Taken By'],
    ['attested_by_user_id', 'Attested By'],
    ['acceptance_expires_on', 'Acceptance Expires'],
    ['implementation_status', 'Status'],
  ])('labels %s as %s', (field, label) => {
    expect(auditFieldLabel(field)).toBe(label)
  })

  it('falls back to the field name for a field it does not know', () => {
    expect(auditFieldLabel('scan_status')).toBe('scan_status')
  })
})

describe('auditEntityTypeLabel', () => {
  it.each([
    ['deficiency', 'Deficiency'],
    ['capability_test', 'Capability test'],
    ['compensating_control', 'Compensating control'],
    ['posture_snapshot', 'Posture snapshot'],
    ['journey_stage', 'Journey stage'],
    ['organization_framework_selection', 'Framework selection'],
    ['scoped_control', 'Scoped control'],
  ])('labels %s as %s', (type, label) => {
    expect(auditEntityTypeLabel(type)).toBe(label)
  })

  // #1156: the filter is built from this list and labelled by the same
  // function as the rows, so every type it offers must read as a phrase.
  it('labels every filterable entity type in sentence case', () => {
    expect(AUDIT_ENTITY_TYPES).toContain('capability_test')
    for (const type of AUDIT_ENTITY_TYPES) {
      const label = auditEntityTypeLabel(type)
      expect(label).not.toContain('_')
      expect(label.slice(1)).toBe(label.slice(1).toLowerCase())
    }
  })
})

describe('formatAuditField', () => {
  // #1156 review: one column, one casing. An enum value no domain map holds
  // reads in sentence case, as the product's own labels do.
  it('reads an enum value without a domain label in sentence case', () => {
    expect(formatAuditField('source', '"capability_test"')).toBe('Capability test')
    expect(formatAuditField('implementation_status', '"at_risk"')).toBe('At risk')
    expect(formatAuditField('effectiveness_rating', '"partial"')).toBe('Partial')
    expect(formatAuditField('status', '"under_review"', { entityType: 'audit_engagement' })).toBe('Under review')
  })

  // #1156: result, status and test type read as the product labels them,
  // from the domain label maps, on the entity those maps describe.
  it('reads result, status and test type through the domain label maps', () => {
    expect(formatAuditField('result', '"passed_with_findings"', { entityType: 'capability_test' })).toBe(
      'Passed with findings',
    )
    expect(formatAuditField('test_type', '"incident_response"', { entityType: 'capability_test' })).toBe(
      'Incident response',
    )
    expect(formatAuditField('status', '"in_progress"', { entityType: 'deficiency' })).toBe('In progress')
  })

  it('reads a status the deficiency map does not hold in sentence case', () => {
    expect(formatAuditField('status', '"under_review"', { entityType: 'deficiency' })).toBe('Under review')
  })

  it('reads an obligation basis as its abbreviation', () => {
    expect(formatAuditField('basis_override', '"mcr"')).toBe('MCR')
    expect(formatAuditField('basis', '"dsr"')).toBe('DSR')
  })

  it('reads the boolean overrides as Yes and No', () => {
    expect(formatAuditField('material_override', 'true')).toBe('Yes')
    expect(formatAuditField('in_scope', 'false')).toBe('No')
    expect(formatAuditField('material_override', 'null')).toBe('—')
  })

  it('reads a validation timestamp as the day the server recorded it', () => {
    expect(formatAuditField('last_validated_at', '"2026-10-13 23:47:12.210318"')).toBe('Oct 13, 2026')
  })

  it('reads a date-only field ending in _on as a date', () => {
    expect(formatAuditField('tested_on', '"2026-10-01"')).toBe('Oct 1, 2026')
    expect(formatAuditField('acceptance_expires_on', '"2026-12-31"')).toBe('Dec 31, 2026')
  })

  it('reads a posture period as its month', () => {
    expect(formatAuditField('period', '"2026-10-01"')).toBe('Oct 2026')
  })

  it('cuts a long value to the length it is given', () => {
    const long = `"${'x'.repeat(100)}"`
    expect(formatAuditField('description', long, { maxLength: 60 })).toHaveLength(60)
    expect(formatAuditField('description', long, { maxLength: 80 })).toHaveLength(80)
    expect(formatAuditField('description', long)).toHaveLength(80)
  })
})

describe('formatAuditField member names', () => {
  const VALIDATOR = 'c73c5103-28a7-4bab-8697-e5eb8799ba0c'
  const FORMER = '00000000-1111-4222-8333-444444444444'
  const members: UserSimple[] = [
    { id: VALIDATOR, email: 'jane@example.test', display_name: 'Jane Doe' },
    { id: 'b1b2c3d4-0000-4000-8000-000000000002', email: 'nodisplay@example.test' },
  ]

  it('names the member a user id refers to', () => {
    expect(formatAuditField('last_validated_by', `"${VALIDATOR}"`, { members })).toBe('Jane Doe')
    expect(formatAuditField('recorded_by_user_id', '"b1b2c3d4-0000-4000-8000-000000000002"', { members })).toBe(
      'nodisplay@example.test',
    )
  })

  // #1156 review: a departed user must stay identifiable, so the short id
  // travels with the words.
  it('calls a user id that is not a member an unknown member, keeping its short id', () => {
    expect(UNKNOWN_MEMBER).toBe('Unknown member')
    expect(formatAuditField('last_validated_by', `"${FORMER}"`, { members })).toBe('Unknown member (00000000…)')
    expect(formatAuditField('scope_override_approved_by', `"${FORMER}"`, { members: [] })).toBe(
      'Unknown member (00000000…)',
    )
  })

  it('reads a user id as loading while the members load', () => {
    expect(formatAuditField('last_validated_by', `"${VALIDATOR}"`, { membersLoading: true })).toBe('Loading…')
    expect(formatAuditField('title', '"Backups"', { membersLoading: true })).toBe('Backups')
  })

  it('leaves a value that is not a user id untouched', () => {
    expect(formatAuditField('last_validated_by', 'null', { members })).toBe('—')
    expect(formatAuditField('last_validated_by', '"api-key"', { members })).toBe('api-key')
    expect(formatAuditField('document_id', `"${VALIDATOR}"`, { members })).toBe(VALIDATOR)
  })

  // A failed member fetch passes no list: the id is shown, not hidden.
  it('leaves a user id as stored when no member list is given', () => {
    expect(formatAuditField('last_validated_by', `"${VALIDATOR}"`)).toBe(VALIDATOR)
  })
})
