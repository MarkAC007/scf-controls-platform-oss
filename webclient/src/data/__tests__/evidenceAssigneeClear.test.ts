/**
 * evidenceAssigneeClear.test.ts — the wire half of the evidence detail page's
 * "Clear" action (item C3).
 *
 * The page no longer offers a way to SET an assignee, only to clear an existing
 * one, and it does that through the PATCH path it already used for every other
 * tracking field: `onUpdateTracking(id, 'assigned_user_id', '')`. That '' has
 * to leave the browser as an explicit `null` — an empty string is rejected by
 * the API's UUID validator, and an omitted key would leave the stored assignee
 * in place, so a Clear button that quietly did nothing is the failure mode.
 *
 * Asserted against the real `updateEvidenceTracking`, not a restatement of it.
 */
import { describe, expect, it, vi, beforeEach } from 'vitest'

const createOrUpdateEvidenceTracking = vi.hoisted(() => vi.fn())

vi.mock('../apiClient', () => ({
  createOrUpdateEvidenceTracking,
}))

import { updateEvidenceTracking } from '../scopingService'
import type { ScopedControlsFile } from '../../types'

const DATA = { organizationId: 'org-1', evidence_tracking: {} } as unknown as ScopedControlsFile

beforeEach(() => {
  createOrUpdateEvidenceTracking.mockReset()
  createOrUpdateEvidenceTracking.mockResolvedValue({ id: 'ev-db-1', evidence_id: 'EHRS01' })
})

describe('clearing the legacy assignee', () => {
  it("sends assigned_user_id: null when the cleared value is ''", async () => {
    await updateEvidenceTracking(
      { ...DATA },
      'EHRS01',
      { is_tracked: true, assigned_user_id: '' },
      'assigned_user_id',
    )
    const payload = createOrUpdateEvidenceTracking.mock.calls[0][0]
    expect(payload).toHaveProperty('assigned_user_id')
    expect(payload.assigned_user_id).toBeNull()
  })

  // Positive control: the same call shape with a real id must send the id, or
  // the assertion above would also pass against a function that nulls
  // everything.
  it('sends the id itself when an assignee is present', async () => {
    await updateEvidenceTracking(
      { ...DATA },
      'EHRS01',
      { is_tracked: true, assigned_user_id: 'user-jane' },
      'assigned_user_id',
    )
    const payload = createOrUpdateEvidenceTracking.mock.calls[0][0]
    expect(payload.assigned_user_id).toBe('user-jane')
  })

  // And the key is omitted entirely for any other field, which is what stops a
  // Comments keystroke from overwriting somebody else's assignment.
  it('omits assigned_user_id when a different field changed', async () => {
    await updateEvidenceTracking(
      { ...DATA },
      'EHRS01',
      { is_tracked: true, assigned_user_id: 'user-jane', comments: 'note' },
      'comments',
    )
    const payload = createOrUpdateEvidenceTracking.mock.calls[0][0]
    expect(payload).not.toHaveProperty('assigned_user_id')
  })
})
