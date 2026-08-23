import { describe, it, expect } from 'vitest'
import { userLabel, evidenceOwnerLabel } from '../userDisplay'
import type { EvidenceTracking } from '../../types'

describe('userLabel', () => {
  it('prefers the display name', () => {
    expect(userLabel({ id: 'u1', email: 'a@b.com', display_name: 'Ada L' })).toBe('Ada L')
  })

  it('falls back to the email when the display name is empty or blank', () => {
    expect(userLabel({ id: 'u1', email: 'a@b.com', display_name: '' })).toBe('a@b.com')
    expect(userLabel({ id: 'u1', email: 'a@b.com', display_name: '   ' })).toBe('a@b.com')
    expect(userLabel({ id: 'u1', email: 'a@b.com' })).toBe('a@b.com')
  })
})

describe('evidenceOwnerLabel', () => {
  const owner = { id: 'u1', email: 'owner@b.com', display_name: 'Owen R' }
  const assignee = { id: 'u2', email: 'assignee@b.com', display_name: 'Asa N' }

  it('names the accountable owner when there is one', () => {
    const t: EvidenceTracking = { owner_user: owner, assigned_user: assignee }
    expect(evidenceOwnerLabel(t)).toBe('Owen R')
  })

  it('falls back to the assignee when nobody is accountable yet', () => {
    expect(evidenceOwnerLabel({ assigned_user: assignee })).toBe('Asa N')
  })

  it('is never empty, so it can be used directly as a group key', () => {
    expect(evidenceOwnerLabel(undefined)).toBe('Unassigned')
    expect(evidenceOwnerLabel(null)).toBe('Unassigned')
    expect(evidenceOwnerLabel({})).toBe('Unassigned')
    expect(evidenceOwnerLabel({ owner_user: null, assigned_user: null })).toBe('Unassigned')
  })

  it('has no free-text fallback: an EvidenceTracking cannot carry one (#781)', () => {
    // @ts-expect-error `owner` was removed from the type; if this ever compiles
    // again, the free-text field has come back and the grouping key with it.
    const t: EvidenceTracking = { owner: 'Security Team' }
    expect(evidenceOwnerLabel(t)).toBe('Unassigned')
  })
})
