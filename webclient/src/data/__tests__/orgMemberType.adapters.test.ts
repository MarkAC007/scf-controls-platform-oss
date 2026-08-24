/**
 * The membership adapters carry ``member_type`` (#822 phase 2, ISC-42).
 *
 * This is the failure mode with no symptom. If the mapping in
 * ``getOrgMemberSummaries`` drops the field, the request still succeeds, the
 * response still has it, every component still renders, nothing is logged and
 * no test that mounts a component with a hand-written fixture goes red —
 * because those fixtures supply ``member_type`` themselves. The only visible
 * effect is that every contractor in the product silently becomes internal.
 * So the adapter is tested here, at the seam between the API's payload and
 * the shape the app passes around, with the network stubbed and nothing above
 * it involved.
 *
 * The three functions are tested together on purpose: two of them are
 * projections of the first, which is the point of the phase-2 change, and a
 * regression that re-fetches independently would show up as an extra request
 * here rather than as a divergence somebody notices months later.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import {
  getOrgMemberSummaries,
  getOrgMembers,
  getOrgMemberships,
  updateOrgMember,
} from '../apiClient'

const ORG_ID = '6a1dad6d-f04e-4f40-bffc-931bc79708da'
const MEMBERS_ENDPOINT = `/api/organizations/${ORG_ID}/members`

const ADA = {
  id: 'm1',
  organization_id: ORG_ID,
  user_id: 'u1',
  role: 'editor',
  member_type: 'external_contractor',
  joined_at: '2026-08-01T00:00:00Z',
  user: { id: 'u1', email: 'ada@example.com', display_name: 'Ada Lovelace' },
}

const GRACE = {
  id: 'm2',
  organization_id: ORG_ID,
  user_id: 'u2',
  role: 'admin',
  member_type: 'internal',
  joined_at: '2026-08-02T00:00:00Z',
  user: { id: 'u2', email: 'grace@example.com', display_name: 'Grace Hopper' },
}

interface Recorded {
  url: string
  method: string
  body: string | null
}

/** Serves one members payload and records every request made. */
function installFetchMock(rows: unknown[]): Recorded[] {
  const calls: Recorded[] = []
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === 'string' ? input : input.toString()
      calls.push({
        url,
        method: String(init?.method ?? 'GET'),
        body: typeof init?.body === 'string' ? init.body : null,
      })
      const payload = url.split('?')[0] === MEMBERS_ENDPOINT ? rows : {}
      return new Response(JSON.stringify(payload), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      })
    })
  )
  return calls
}

beforeEach(() => {
  localStorage.clear()
})

afterEach(() => {
  vi.unstubAllGlobals()
  localStorage.clear()
})

describe('getOrgMemberSummaries', () => {
  it('carries the contractor value through untouched', async () => {
    installFetchMock([ADA, GRACE])
    const summaries = await getOrgMemberSummaries(ORG_ID)
    expect(summaries.map(s => [s.user_id, s.member_type])).toEqual([
      ['u1', 'external_contractor'],
      ['u2', 'internal'],
    ])
  })

  it('reads a missing member_type as internal', async () => {
    // A server that has not shipped the column returns rows without it. The
    // honest reading of a missing label is the column's own default, and it
    // must be a concrete value: `undefined` would flow into components that
    // compare against the two legal strings and behave like a third state.
    const { member_type: _dropped, ...withoutType } = ADA
    installFetchMock([withoutType])
    const [summary] = await getOrgMemberSummaries(ORG_ID)
    expect(summary.member_type).toBe('internal')
  })

  it('reads an unrecognised member_type as internal', async () => {
    // Nothing should ever send one — a CHECK constraint stands behind the
    // column. If something does, the safe reading is the one that does not
    // brand somebody a contractor on the strength of a value nobody defined.
    installFetchMock([{ ...ADA, member_type: 'CONTRACTOR' }])
    const [summary] = await getOrgMemberSummaries(ORG_ID)
    expect(summary.member_type).toBe('internal')
  })

  it('keeps the rest of the membership alongside it', async () => {
    // The type is only useful attached to a person and a role.
    installFetchMock([ADA])
    const [summary] = await getOrgMemberSummaries(ORG_ID)
    expect(summary).toMatchObject({
      id: 'm1',
      organization_id: ORG_ID,
      user_id: 'u1',
      role: 'editor',
      member_type: 'external_contractor',
    })
    expect(summary.user?.display_name).toBe('Ada Lovelace')
  })
})

describe('getOrgMemberships', () => {
  it('carries member_type', async () => {
    installFetchMock([ADA, GRACE])
    const memberships = await getOrgMemberships(ORG_ID)
    expect(memberships).toEqual([
      { user_id: 'u1', role: 'editor', member_type: 'external_contractor' },
      { user_id: 'u2', role: 'admin', member_type: 'internal' },
    ])
  })

  it('keeps role and member_type independent', async () => {
    // A contractor who is an admin is an admin. If these two were ever
    // conflated the damage would be invisible here and severe elsewhere.
    installFetchMock([{ ...ADA, role: 'admin', member_type: 'external_contractor' }])
    const [membership] = await getOrgMemberships(ORG_ID)
    expect(membership.role).toBe('admin')
    expect(membership.member_type).toBe('external_contractor')
  })
})

describe('getOrgMembers', () => {
  it('returns the people, without the membership around them', async () => {
    // The discard is deliberate: this feeds pickers that name a person and
    // nothing else. member_type belongs to a membership, and a per-org label
    // smuggled onto a user object reads as a property of the person.
    installFetchMock([ADA, GRACE])
    const users = await getOrgMembers(ORG_ID)
    expect(users.map(u => u.id)).toEqual(['u1', 'u2'])
    expect(users[0]).not.toHaveProperty('member_type')
    expect(users[0]).not.toHaveProperty('role')
  })

  it('drops a membership whose user did not come back', async () => {
    installFetchMock([{ ...ADA, user: null }, GRACE])
    const users = await getOrgMembers(ORG_ID)
    expect(users.map(u => u.id)).toEqual(['u2'])
  })
})

describe('one URL behind all three', () => {
  it('every adapter reads the same members endpoint', async () => {
    // Phase 2 folded two independent fetches into one so that a payload that
    // grows a field grows it in one place. A second URL appearing here means
    // that has come apart.
    const calls = installFetchMock([ADA])
    await getOrgMemberSummaries(ORG_ID)
    await getOrgMembers(ORG_ID)
    await getOrgMemberships(ORG_ID)
    expect(calls.map(c => c.url)).toEqual([
      MEMBERS_ENDPOINT,
      MEMBERS_ENDPOINT,
      MEMBERS_ENDPOINT,
    ])
  })
})

describe('updateOrgMember', () => {
  it('sends member_type as a query parameter', async () => {
    // Deliberately unlike the invite endpoint, which takes it in the JSON
    // body. Two shapes, and sending either one to the other endpoint is
    // ignored rather than refused.
    const calls = installFetchMock([])
    await updateOrgMember(ORG_ID, 'u1', { member_type: 'external_contractor' })
    expect(calls).toHaveLength(1)
    expect(calls[0].method).toBe('PATCH')
    const url = new URL(calls[0].url, 'http://localhost')
    expect(url.pathname).toBe(`/api/organizations/${ORG_ID}/members/u1`)
    expect(url.searchParams.get('member_type')).toBe('external_contractor')
  })

  it('does not send a role that was not asked for', async () => {
    // The caller holds a possibly-stale copy of the role. Sending it back
    // alongside the type would revert another admin's change in silence.
    const calls = installFetchMock([])
    await updateOrgMember(ORG_ID, 'u1', { member_type: 'internal' })
    const url = new URL(calls[0].url, 'http://localhost')
    expect(url.searchParams.has('role')).toBe(false)
  })

  it('sends both when both were asked for', async () => {
    const calls = installFetchMock([])
    await updateOrgMember(ORG_ID, 'u1', { role: 'admin', member_type: 'internal' })
    const url = new URL(calls[0].url, 'http://localhost')
    expect(url.searchParams.get('role')).toBe('admin')
    expect(url.searchParams.get('member_type')).toBe('internal')
  })

  it('makes no request at all when nothing changed', async () => {
    const calls = installFetchMock([])
    await updateOrgMember(ORG_ID, 'u1', {})
    expect(calls).toEqual([])
  })
})
