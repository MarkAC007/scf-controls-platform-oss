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

  it('holds the picker disabled until the creator role is known', async () => {
    rank = 'pending'
    const select = await openCreateModal()
    expect(select).toBeDisabled()
    expect(optionValues(select)).toEqual([])
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
