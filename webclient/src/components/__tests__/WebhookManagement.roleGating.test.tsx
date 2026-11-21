// SPDX-License-Identifier: AGPL-3.0-only
/**
 * WebhookManagement: only an admin may list or manage webhook endpoints (#1273).
 *
 * GET …/webhook-endpoints is admin-only. An editor used to trigger it, get a
 * 403 and an "Access denied" toast, and then read "0 endpoints … No webhook
 * endpoints configured", which states something the page does not know. A
 * non-admin is now told who can see the endpoints and the list is not requested.
 */
import { render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import WebhookManagement from '../WebhookManagement'
import { listWebhookEndpoints } from '../../data/apiClient'

const role = vi.hoisted(() => ({ rank: 'admin' as 'viewer' | 'editor' | 'admin' | 'unknown' }))
vi.mock('../../hooks/useHasOrgRole', () => {
  const order: Record<string, number> = { viewer: 0, editor: 1, admin: 2 }
  // 'unknown' is the window before the membership lookup returns: nothing is allowed yet.
  const has = (_org: unknown, min: 'viewer' | 'editor' | 'admin') => order[role.rank] >= order[min]
  return {
    useHasOrgRole: has,
    useIsOrgEditor: (org: unknown) => has(org, 'editor'),
    useOrgRoleState: (org: unknown, min: 'viewer' | 'editor' | 'admin') => ({
      allowed: has(org, min),
      resolved: role.rank !== 'unknown',
    }),
  }
})
vi.mock('../../data/apiClient', () => ({
  listWebhookEndpoints: vi.fn(),
  rotateWebhookSecret: vi.fn(),
  revokeWebhookEndpoint: vi.fn(),
  getWebhookDeliveries: vi.fn(),
}))
vi.mock('react-hot-toast', () => ({ toast: Object.assign(vi.fn(), { success: vi.fn(), error: vi.fn() }) }))

const ENDPOINT = {
  id: 'wh-1', name: 'Okta export', description: null, secret_prefix: 'whsec_ab', allowed_evidence_ids: ['E-IAM-01'],
  is_active: true, delivery_count: 3, last_delivery_at: null, created_at: '2026-01-01T00:00:00Z',
}

beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(listWebhookEndpoints).mockResolvedValue([ENDPOINT] as never)
})
afterEach(() => { role.rank = 'admin' })

describe('WebhookManagement role gating', () => {
  it.each(['viewer', 'editor'] as const)('tells a %s the list is for admins, and does not request it', async (rank) => {
    role.rank = rank
    render(<WebhookManagement organizationId="org-1" />)

    expect(screen.getByTestId('webhooks-admin-only')).toHaveTextContent(
      'Only an admin of this organisation can see or manage its webhook endpoints'
    )
    await new Promise(resolve => setTimeout(resolve, 0))
    expect(listWebhookEndpoints).not.toHaveBeenCalled()
    // Nothing that reads as "this organisation has none".
    const text = document.body.textContent ?? ''
    expect(text).not.toMatch(/No webhook endpoints configured/)
    expect(text).not.toMatch(/\d+ endpoints?\b/)
    expect(screen.queryByRole('button', { name: /Create \(via Wizard\)/ })).not.toBeInTheDocument()
    expect(screen.queryByRole('searchbox')).not.toBeInTheDocument()
  })

  // An admin used to read the notice above on every visit, until the
  // membership lookup returned. While the role is not known the page says
  // nothing about who may see the list, shows no toolbar and requests nothing.
  it('shows only a spinner while the role is not yet known, then the list once it is admin', async () => {
    role.rank = 'unknown'
    const { rerender } = render(<WebhookManagement organizationId="org-1" />)

    expect(screen.queryByTestId('webhooks-admin-only')).not.toBeInTheDocument()
    expect(document.querySelector('.loading-spinner')).not.toBeNull()
    const text = document.body.textContent ?? ''
    expect(text).not.toMatch(/Only an admin/)
    expect(text).not.toMatch(/No webhook endpoints configured/)
    expect(text).not.toMatch(/\d+ endpoints?\b/)
    expect(screen.queryByRole('button', { name: /Create \(via Wizard\)/ })).not.toBeInTheDocument()
    await new Promise(resolve => setTimeout(resolve, 0))
    expect(listWebhookEndpoints).not.toHaveBeenCalled()

    role.rank = 'admin'
    rerender(<WebhookManagement organizationId="org-1" />)
    expect(await screen.findByText('Okta export')).toBeInTheDocument()
    expect(listWebhookEndpoints).toHaveBeenCalledTimes(1)
    expect(screen.queryByTestId('webhooks-admin-only')).not.toBeInTheDocument()
  })

  it('lists the endpoints for an admin, with the toolbar', async () => {
    role.rank = 'admin'
    render(<WebhookManagement organizationId="org-1" />)

    expect(await screen.findByText('Okta export')).toBeInTheDocument()
    expect(listWebhookEndpoints).toHaveBeenCalledWith('org-1')
    expect(screen.getByText('1 endpoint')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Create \(via Wizard\)/ })).toBeEnabled()
    expect(screen.queryByTestId('webhooks-admin-only')).not.toBeInTheDocument()
  })

  it('still tells an admin with no endpoints that there are none', async () => {
    role.rank = 'admin'
    vi.mocked(listWebhookEndpoints).mockResolvedValue([])
    render(<WebhookManagement organizationId="org-1" />)
    await waitFor(() => expect(screen.getByText(/No webhook endpoints configured/)).toBeInTheDocument())
    expect(screen.getByText('0 endpoints')).toBeInTheDocument()
  })
})
