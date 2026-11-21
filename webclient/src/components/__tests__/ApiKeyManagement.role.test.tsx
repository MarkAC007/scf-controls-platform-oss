// SPDX-License-Identifier: AGPL-3.0-only
/**
 * ApiKeyManagement — choosing a key's role at creation (#1117).
 *
 * What these tests defend:
 *
 *   - the picker offers every rank up to the creator's own and no further:
 *     an admin sees viewer/editor/admin, an editor sees viewer/editor;
 *   - it defaults to the creator's own role, so nothing changes for anyone
 *     who ignores it;
 *   - until the creator's role has resolved the select is disabled rather
 *     than defaulting to viewer;
 *   - the chosen role is what the create call sends, and an untouched picker
 *     still sends the creator's role explicitly.
 */
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import ApiKeyManagement from '../ApiKeyManagement'
import { createOrgApiKey, getOrgApiKeys } from '../../data/apiClient'

vi.mock('../../data/apiClient', () => ({
  getOrgApiKeys: vi.fn(),
  createOrgApiKey: vi.fn(),
  revokeOrgApiKey: vi.fn(),
}))

let rank: 'admin' | 'editor' | 'viewer' | 'pending' = 'admin'
vi.mock('../../hooks/useHasOrgRole', () => ({
  useHasOrgRole: (_org: string, min: 'admin' | 'editor' | 'viewer') => {
    if (rank === 'pending') return false
    const order = { viewer: 0, editor: 1, admin: 2 }
    return order[rank] >= order[min]
  },
  // 'pending' is the window before the membership lookup returns.
  useOrgRoleState: (_org: string, min: 'admin' | 'editor' | 'viewer') => {
    if (rank === 'pending') return { allowed: false, resolved: false }
    const order = { viewer: 0, editor: 1, admin: 2 }
    return { allowed: order[rank] >= order[min], resolved: true }
  },
}))

vi.mock('react-hot-toast', () => ({
  toast: { success: vi.fn(), error: vi.fn() },
}))

const mockList = vi.mocked(getOrgApiKeys)
const mockCreate = vi.mocked(createOrgApiKey)

const ORG = 'org-1'

async function openCreateModal() {
  render(<ApiKeyManagement organizationId={ORG} />)
  await waitFor(() => expect(mockList).toHaveBeenCalled())
  await userEvent.click(screen.getByRole('button', { name: 'Create key' }))
  return screen.getByLabelText('Role') as HTMLSelectElement
}

function optionValues(select: HTMLSelectElement): string[] {
  return Array.from(select.options).map(o => o.value).filter(Boolean)
}

beforeEach(() => {
  vi.clearAllMocks()
  mockList.mockResolvedValue([])
  mockCreate.mockResolvedValue({
    id: 'k1', name: 'ro', key_prefix: 'scf_abcd', role: 'viewer', is_active: true,
    expires_at: null, last_used_at: null, created_at: '2026-10-04T00:00:00Z',
    user_id: 'u1', user_email: 'u@example.invalid',
    plaintext_key: 'scf_' + 'a'.repeat(36), warning: '',
  })
})

describe('ApiKeyManagement — key role picker', () => {
  it('offers an admin every rank and defaults to admin', async () => {
    rank = 'admin'
    const select = await openCreateModal()
    expect(optionValues(select)).toEqual(['viewer', 'editor', 'admin'])
    expect(select.value).toBe('admin')
    expect(select).not.toBeDisabled()
  })

  it('caps an editor at editor and defaults to editor', async () => {
    rank = 'editor'
    const select = await openCreateModal()
    expect(optionValues(select)).toEqual(['viewer', 'editor'])
    expect(select.value).toBe('editor')
  })

  // Until the role is known the hook reads false, exactly as it does for a
  // viewer, so "Create key" is withheld rather than opening a form whose role
  // picker could only sit disabled (#1273).
  it.each(['pending', 'viewer'] as const)('does not offer Create key or Revoke while the role is %s', async (r) => {
    rank = r
    mockList.mockResolvedValue([
      { id: 'k1', name: 'feed', key_prefix: 'scf_abc', role: 'viewer', is_active: true, created_at: '2026-01-01T00:00:00Z' },
    ] as never)
    render(<ApiKeyManagement organizationId={ORG} />)
    expect(await screen.findByText('feed')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Create key' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Revoke' })).not.toBeInTheDocument()
  })

  // The empty state names the missing role only once it is known to be missing:
  // an editor must not read "needs the editor role" while the lookup is out.
  it('says only that there are no keys while the role is pending', async () => {
    rank = 'pending'
    render(<ApiKeyManagement organizationId={ORG} />)
    expect(await screen.findByText('No API keys yet.')).toBeInTheDocument()
    expect(screen.queryByText(/needs the editor role/)).not.toBeInTheDocument()
  })

  it('tells a viewer that creating a key needs the editor role', async () => {
    rank = 'viewer'
    render(<ApiKeyManagement organizationId={ORG} />)
    expect(await screen.findByText('No API keys yet. Creating one needs the editor role.')).toBeInTheDocument()
  })

  it('offers an editor Create key and Revoke', async () => {
    rank = 'editor'
    mockList.mockResolvedValue([
      { id: 'k1', name: 'feed', key_prefix: 'scf_abc', role: 'viewer', is_active: true, created_at: '2026-01-01T00:00:00Z' },
    ] as never)
    render(<ApiKeyManagement organizationId={ORG} />)
    expect(await screen.findByText('feed')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Create key' })).toBeEnabled()
    expect(screen.getByRole('button', { name: 'Revoke' })).toBeEnabled()
  })

  it('sends the chosen role on create', async () => {
    rank = 'admin'
    const select = await openCreateModal()
    await userEvent.type(screen.getByLabelText('Key Name *'), 'trust portal feed')
    await userEvent.selectOptions(select, 'viewer')
    await userEvent.click(screen.getByRole('button', { name: /Create Key/ }))
    await waitFor(() => expect(mockCreate).toHaveBeenCalled())
    expect(mockCreate).toHaveBeenCalledWith(ORG, 'trust portal feed', undefined, 'viewer')
  })

  it('sends the creator role when the picker is left alone', async () => {
    rank = 'editor'
    await openCreateModal()
    await userEvent.type(screen.getByLabelText('Key Name *'), 'ci')
    await userEvent.click(screen.getByRole('button', { name: /Create Key/ }))
    await waitFor(() => expect(mockCreate).toHaveBeenCalled())
    expect(mockCreate).toHaveBeenCalledWith(ORG, 'ci', undefined, 'editor')
  })

  it('no longer claims the key inherits the current role', async () => {
    rank = 'admin'
    await openCreateModal()
    expect(screen.queryByText(/inherit your current role/i)).toBeNull()
    expect(screen.getByText(/can never exceed your own role/i)).toBeInTheDocument()
  })
})
