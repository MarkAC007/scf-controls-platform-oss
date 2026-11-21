// SPDX-License-Identifier: AGPL-3.0-only
/**
 * RiskProfileSettings: who may change the risk profile and the trust portal (#1273).
 *
 * PUT …/risk-profile, POST …/risk-profile/reset and PATCH …/settings are
 * admin-only on the API. Anyone else reads the settings and is offered no
 * control that would answer 403.
 */
import { render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import RiskProfileSettings from '../RiskProfileSettings'

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

// Stable objects: the component syncs its form from these in effects, so a
// fresh object per render would loop.
const fixtures = vi.hoisted(() => ({
  profile: {
    riskProfile: {
      id: 'rp-1', organization_id: 'org-1', low_max: 4, medium_max: 9, high_max: 16,
      acceptable_risk_level: 'medium', auto_escalate_above: 'high',
      required_vendor_certifications: '["ISO 27001"]', preferred_vendor_certifications: '[]',
      vendor_auto_approve_max: 5, vendor_auto_reject_min: 20,
      created_at: '2026-01-01T00:00:00Z', updated_at: '2026-01-01T00:00:00Z',
    },
    isLoading: false,
    error: null,
    updateProfile: () => Promise.resolve(),
    resetProfile: () => Promise.resolve(),
    refreshProfile: () => Promise.resolve(),
  },
  settings: {
    data: { is_trust_portal_enabled: true, trust_portal_description: 'We take security seriously.' },
    refetch: () => Promise.resolve(),
  },
}))
vi.mock('../../contexts/RiskProfileContext', () => ({ useRiskProfile: () => fixtures.profile }))
vi.mock('../../contexts/AuthContext', () => ({ useAuth: () => ({ user: null }) }))
vi.mock('../../hooks/useOrganizationSettings', () => ({ useOrganizationSettings: () => fixtures.settings }))
vi.mock('../../data/apiClient', () => ({ updateOrganizationSettings: vi.fn() }))
vi.mock('react-hot-toast', () => ({ toast: { success: vi.fn(), error: vi.fn() } }))

afterEach(() => { role.rank = 'admin' })

/** Every form control on the card, the trust portal's included. */
const controls = () => Array.from(document.querySelectorAll('.risk-profile-settings button, .risk-profile-settings input, .risk-profile-settings select, .risk-profile-settings textarea'))

describe('RiskProfileSettings role gating', () => {
  it.each(['viewer', 'editor'] as const)('shows a %s the settings with every control switched off', (rank) => {
    role.rank = rank
    render(<RiskProfileSettings organizationId="org-1" />)

    expect(screen.getByTestId('risk-profile-admin-only')).toHaveTextContent(
      'Only an organisation administrator can change the risk profile or the trust portal.'
    )
    // Still readable.
    expect(screen.getByLabelText('High Maximum')).toHaveValue(16)
    expect(screen.getByLabelText('Portal Description')).toHaveValue('We take security seriously.')
    // Nothing usable: Reset to Defaults and Save Trust Portal Settings are
    // enabled at rest for an admin, so these two are the ones that matter.
    expect(screen.getByRole('button', { name: 'Reset to Defaults' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Save Trust Portal Settings' })).toBeDisabled()
    expect(screen.getByRole('switch')).toBeDisabled()
    expect(controls().length).toBeGreaterThan(8)
    controls().forEach(control => expect(control).toBeDisabled())
  })

  it('keeps every control off but says nothing about admins while the role is not yet known', () => {
    role.rank = 'unknown'
    render(<RiskProfileSettings organizationId="org-1" />)

    expect(screen.queryByTestId('risk-profile-admin-only')).not.toBeInTheDocument()
    expect(screen.getByLabelText('High Maximum')).toHaveValue(16)
    expect(controls().length).toBeGreaterThan(8)
    controls().forEach(control => expect(control).toBeDisabled())
  })

  it('leaves an admin the form as it was', () => {
    role.rank = 'admin'
    render(<RiskProfileSettings organizationId="org-1" />)

    expect(screen.queryByTestId('risk-profile-admin-only')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Reset to Defaults' })).toBeEnabled()
    expect(screen.getByRole('button', { name: 'Save Trust Portal Settings' })).toBeEnabled()
    expect(screen.getByRole('switch')).toBeEnabled()
    expect(screen.getByLabelText('High Maximum')).toBeEnabled()
    // Save Changes waits for an edit, for an admin as for anyone.
    expect(screen.getByRole('button', { name: 'Save Changes' })).toBeDisabled()
  })
})
