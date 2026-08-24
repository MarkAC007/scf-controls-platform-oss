/**
 * The accountable-owner-type filter, at the hook that runs it
 * (#822 phase 2, ISC-42).
 *
 * This hook has one property that makes it worth testing on its own: it
 * distinguishes "narrowed to nothing" from "not narrowing". Both look like
 * absence. ``null`` means the caller should fall back to the assignment map it
 * already holds, and an EMPTY SET means the server was asked and answered that
 * nothing matches. A Set is truthy whether or not it has members, so the two
 * are only distinguishable if the success path keeps returning a Set — the
 * moment an empty answer is turned into ``null`` for tidiness, filtering to
 * "contractor-owned" in an organisation that has none shows the caller
 * EVERYTHING instead of nothing, which is the opposite answer and looks
 * entirely plausible on screen.
 *
 * A team with no primary owner is a permanent, legal state, not an error, so
 * that empty answer is a normal day rather than an edge case.
 */
import { renderHook, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { useTeamFilteredEvidence } from '../useTeamFilteredEvidence'
import { getEvidenceTracking } from '../../data/apiClient'

vi.mock('../../data/apiClient', () => ({
  getEvidenceTracking: vi.fn(),
}))

const fetchTracking = vi.mocked(getEvidenceTracking)
const ORG_ID = '11111111-1111-4111-8111-111111111111'

beforeEach(() => {
  vi.clearAllMocks()
  fetchTracking.mockResolvedValue([] as never)
})

afterEach(() => {
  vi.restoreAllMocks()
})

describe('when nothing is filtered', () => {
  it('asks for nothing and narrows nothing', async () => {
    const { result } = renderHook(() =>
      useTeamFilteredEvidence(ORG_ID, undefined, undefined, undefined)
    )
    expect(result.current.trackingIds).toBeNull()
    expect(fetchTracking).not.toHaveBeenCalled()
  })

  it('asks for nothing without an organisation', async () => {
    renderHook(() =>
      useTeamFilteredEvidence(null, undefined, undefined, 'external_contractor')
    )
    expect(fetchTracking).not.toHaveBeenCalled()
  })
})

describe('when only the owner type is chosen', () => {
  it('counts as a filter on its own', async () => {
    // The guard predates this parameter. If it still only looked at team and
    // function, choosing "contractor-owned" would fetch nothing, leave
    // trackingIds null, and quietly show the unfiltered list.
    renderHook(() =>
      useTeamFilteredEvidence(ORG_ID, undefined, undefined, 'external_contractor')
    )
    await waitFor(() => expect(fetchTracking).toHaveBeenCalled())
    expect(fetchTracking).toHaveBeenCalledWith(ORG_ID, {
      team_id: undefined,
      function_id: undefined,
      accountable_owner_type: 'external_contractor',
    })
  })

  it('narrows to nothing rather than to everything when nothing matches', async () => {
    // The whole point of this file. An organisation with no contractor-led
    // team is the common case, and the honest answer is an empty list.
    fetchTracking.mockResolvedValue([] as never)
    const { result } = renderHook(() =>
      useTeamFilteredEvidence(ORG_ID, undefined, undefined, 'external_contractor')
    )
    await waitFor(() => expect(result.current.loading).toBe(false))
    expect(result.current.trackingIds).toBeInstanceOf(Set)
    expect(result.current.trackingIds?.size).toBe(0)
    expect(result.current.trackingIds).not.toBeNull()
  })

  it('returns the matching ids when some match', async () => {
    fetchTracking.mockResolvedValue([{ id: 't1' }, { id: 't2' }] as never)
    const { result } = renderHook(() =>
      useTeamFilteredEvidence(ORG_ID, undefined, undefined, 'external_contractor')
    )
    await waitFor(() => expect(result.current.trackingIds?.size).toBe(2))
    expect([...(result.current.trackingIds ?? [])]).toEqual(['t1', 't2'])
  })

  it('drops rows the server sent without an id', async () => {
    // A Set containing undefined would match nothing and cost an entry.
    fetchTracking.mockResolvedValue([{ id: 't1' }, { id: null }, {}] as never)
    const { result } = renderHook(() =>
      useTeamFilteredEvidence(ORG_ID, undefined, undefined, 'internal')
    )
    await waitFor(() => expect(result.current.loading).toBe(false))
    expect([...(result.current.trackingIds ?? [])]).toEqual(['t1'])
  })
})

describe('alongside the team filter', () => {
  it('sends both, for the server to combine', async () => {
    // Two predicates, both applied. Sending only one would widen the answer
    // and look like a working filter.
    renderHook(() =>
      useTeamFilteredEvidence(ORG_ID, 'team-1', undefined, 'external_contractor')
    )
    await waitFor(() => expect(fetchTracking).toHaveBeenCalled())
    expect(fetchTracking).toHaveBeenCalledWith(ORG_ID, {
      team_id: 'team-1',
      function_id: undefined,
      accountable_owner_type: 'external_contractor',
    })
  })

  it('refetches when only the owner type changes', async () => {
    const { rerender } = renderHook(
      ({ type }) => useTeamFilteredEvidence(ORG_ID, 'team-1', undefined, type),
      { initialProps: { type: 'external_contractor' as const } }
    )
    await waitFor(() => expect(fetchTracking).toHaveBeenCalledTimes(1))
    rerender({ type: 'internal' as never })
    await waitFor(() => expect(fetchTracking).toHaveBeenCalledTimes(2))
    expect(fetchTracking.mock.calls[1][1]).toMatchObject({
      accountable_owner_type: 'internal',
    })
  })
})

describe('when the request fails', () => {
  it('un-narrows and says why', async () => {
    // Documented degrade: the caller has a client-side assignment map that
    // agrees about teams, so falling back beats blanking the list. The error
    // is what stops that fallback being silent, because nothing client-side
    // knows who leads an accountable team.
    fetchTracking.mockRejectedValue(new Error('boom'))
    const { result } = renderHook(() =>
      useTeamFilteredEvidence(ORG_ID, undefined, undefined, 'external_contractor')
    )
    await waitFor(() => expect(result.current.error).toBe('boom'))
    expect(result.current.trackingIds).toBeNull()
    expect(result.current.loading).toBe(false)
  })

  it('clears a stale error once a later filter succeeds', async () => {
    fetchTracking.mockRejectedValueOnce(new Error('boom'))
    const { result, rerender } = renderHook(
      ({ team }) => useTeamFilteredEvidence(ORG_ID, team, undefined, 'internal'),
      { initialProps: { team: 'team-1' } }
    )
    await waitFor(() => expect(result.current.error).toBe('boom'))
    fetchTracking.mockResolvedValue([{ id: 't9' }] as never)
    rerender({ team: 'team-2' })
    await waitFor(() => expect(result.current.error).toBeNull())
    expect([...(result.current.trackingIds ?? [])]).toEqual(['t9'])
  })
})

describe('when the filter changes mid-flight', () => {
  it('does not let the earlier answer land on top of the later one', async () => {
    // Otherwise switching from contractor to internal can leave the
    // contractor result on screen under the internal label.
    let releaseFirst: (rows: unknown[]) => void = () => {}
    fetchTracking.mockImplementationOnce(
      () => new Promise(resolve => { releaseFirst = resolve as never }) as never
    )

    const { result, rerender } = renderHook(
      ({ type }) => useTeamFilteredEvidence(ORG_ID, undefined, undefined, type),
      { initialProps: { type: 'external_contractor' as const } }
    )

    fetchTracking.mockResolvedValue([{ id: 'second' }] as never)
    rerender({ type: 'internal' as never })
    await waitFor(() => expect(result.current.trackingIds?.has('second')).toBe(true))

    releaseFirst([{ id: 'first' }])
    await new Promise(resolve => setTimeout(resolve, 0))
    expect([...(result.current.trackingIds ?? [])]).toEqual(['second'])
  })
})
