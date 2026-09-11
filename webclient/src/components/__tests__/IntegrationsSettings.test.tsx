/**
 * IntegrationsSettings — Settings → Integrations (platform admin).
 *
 * The screen is write-only: the API never returns a stored credential and the
 * component must never render one. These tests pin that guarantee alongside the
 * six-row inventory, the Replace/Clear round trips, the missing-encryption-key
 * banner, the operator-managed lockout and the 409 detail surfacing.
 */
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import IntegrationsSettings from '../IntegrationsSettings'
import {
  listIntegrations,
  setIntegration,
  clearIntegration,
  getIntegrationsHealth,
  getIntegrationsAudit,
} from '../../data/integrationsApi'
import type {
  IntegrationItem,
  IntegrationsAuditResponse,
  IntegrationsHealthResponse,
  IntegrationsListResponse,
} from '../../data/integrationsApi'

vi.mock('../../data/integrationsApi', () => ({
  listIntegrations: vi.fn(),
  setIntegration: vi.fn(),
  clearIntegration: vi.fn(),
  getIntegrationsHealth: vi.fn(),
  getIntegrationsAudit: vi.fn(),
}))

const mockList = vi.mocked(listIntegrations)
const mockSet = vi.mocked(setIntegration)
const mockClear = vi.mocked(clearIntegration)
const mockHealth = vi.mocked(getIntegrationsHealth)
const mockAudit = vi.mocked(getIntegrationsAudit)

/** The stored plaintext must never reach the DOM, from any code path. */
const STORED_PLAINTEXT = 'sk-ant-super-secret-do-not-render'

function item(overrides: Partial<IntegrationItem> & { name: string }): IntegrationItem {
  return {
    label: overrides.name,
    feature: `What ${overrides.name} unlocks`,
    configured: false,
    source: null,
    managed_by_operator: false,
    updated_at: null,
    updated_by: null,
    ...overrides,
  }
}

/** The six tier-3 credentials, in contract order (CONTRACT.md §3d LABELS). */
function sixItems(): IntegrationItem[] {
  return [
    item({
      name: 'OIDC_CLIENT_SECRET',
      label: 'OIDC client secret',
      feature: 'Single sign-on with an external identity provider',
      configured: true,
      source: 'file',
      managed_by_operator: true,
      updated_at: '2026-09-01T10:00:00Z',
      updated_by: 'operator',
    }),
    item({
      name: 'RESEND_API_KEY',
      label: 'Resend API key',
      feature: 'Transactional email: invitations, notifications',
      configured: true,
      source: 'db',
      updated_at: '2026-09-02T11:30:00Z',
      updated_by: 'mark@example.com',
    }),
    item({
      name: 'ANTHROPIC_API_KEY',
      label: 'Anthropic API key',
      feature: 'AI document generation and evidence assessment',
    }),
    item({
      name: 'AZURE_STORAGE_ACCOUNT_KEY',
      label: 'Azure Storage account key',
      feature: 'Evidence storage on Azure Blob',
    }),
    item({
      name: 'HIBP_API_KEY',
      label: 'Have I Been Pwned API key',
      feature: 'Vendor breach research',
      configured: true,
      source: 'env',
      managed_by_operator: true,
    }),
    item({
      name: 'NVD_API_KEY',
      label: 'NVD API key',
      feature: 'Higher NVD rate limit for vendor CVE research',
    }),
  ]
}

function listResponse(overrides: Partial<IntegrationsListResponse> = {}): IntegrationsListResponse {
  return {
    encryption_key_configured: true,
    legacy_plaintext_rows: 0,
    items: sixItems(),
    ...overrides,
  }
}

function healthResponse(
  overrides: Partial<IntegrationsHealthResponse> = {}
): IntegrationsHealthResponse {
  const items = overrides.items ?? sixItems()
  return {
    encryption_key_configured: true,
    configured: items.filter((i) => i.configured).map((i) => i.name),
    unconfigured: items.filter((i) => !i.configured).map((i) => i.name),
    items,
    secrets_dir_mode: 'file',
    ...overrides,
  }
}

function auditResponse(overrides: Partial<IntegrationsAuditResponse> = {}): IntegrationsAuditResponse {
  return {
    items: [
      {
        action: 'integration.secret.replaced',
        entity_id: 'RESEND_API_KEY',
        actor: 'mark@example.com',
        created_at: '2026-09-02T11:30:00Z',
        ip_address: '10.0.0.4',
        action_source: 'ui',
      },
      {
        action: 'integration.secret.cleared',
        entity_id: 'NVD_API_KEY',
        actor: 'api_key:master',
        created_at: '2026-09-01T09:00:00Z',
        ip_address: null,
        action_source: 'api',
      },
    ],
    ...overrides,
  }
}

/** An error carrying an HTTP status, the shape integrationsApi throws. */
function apiError(status: number, message: string): Error & { status: number } {
  const err = new Error(message) as Error & { status: number }
  err.status = status
  return err
}

beforeEach(() => {
  vi.clearAllMocks()
  mockList.mockResolvedValue(listResponse())
  mockHealth.mockResolvedValue(healthResponse())
  mockAudit.mockResolvedValue(auditResponse())
})

/** Wait for the initial load to settle. */
async function renderLoaded() {
  const view = render(<IntegrationsSettings />)
  await waitFor(() => {
    expect(screen.getByTestId('integration-row-RESEND_API_KEY')).toBeInTheDocument()
  })
  return view
}

describe('IntegrationsSettings', () => {
  it('renders one row per tier-3 credential with label, feature, badge and source chip', async () => {
    await renderLoaded()

    for (const expected of sixItems()) {
      const row = screen.getByTestId(`integration-row-${expected.name}`)
      expect(within(row).getByText(expected.label)).toBeInTheDocument()
      expect(within(row).getByText(expected.feature)).toBeInTheDocument()
    }

    // Exactly six rows — no extras, nothing dropped.
    expect(screen.getAllByTestId(/^integration-row-/)).toHaveLength(6)

    const resend = screen.getByTestId('integration-row-RESEND_API_KEY')
    expect(within(resend).getByText('Configured')).toBeInTheDocument()
    expect(within(resend).getByText('Managed in app')).toBeInTheDocument()

    const anthropic = screen.getByTestId('integration-row-ANTHROPIC_API_KEY')
    expect(within(anthropic).getByText('Not configured')).toBeInTheDocument()

    const oidc = screen.getByTestId('integration-row-OIDC_CLIENT_SECRET')
    expect(within(oidc).getByText('Managed by operator (file)')).toBeInTheDocument()

    const hibp = screen.getByTestId('integration-row-HIBP_API_KEY')
    expect(within(hibp).getByText('Managed by operator (env)')).toBeInTheDocument()
  })

  it('masks configured rows and never renders a stored value', async () => {
    // Simulate a backend that wrongly leaks a value on the item: the component
    // must still render only the mask.
    const leaky = sixItems().map((i) =>
      i.name === 'RESEND_API_KEY' ? { ...i, value: STORED_PLAINTEXT } : i
    ) as IntegrationItem[]
    mockList.mockResolvedValue(listResponse({ items: leaky }))

    await renderLoaded()

    const row = screen.getByTestId('integration-row-RESEND_API_KEY')
    const mask = within(row).getByText('••••••••')
    expect(mask).toBeInTheDocument()
    expect(mask.textContent).not.toBe(STORED_PLAINTEXT)
    expect(document.body.textContent).not.toContain(STORED_PLAINTEXT)

    // Unconfigured rows get no mask at all.
    const anthropic = screen.getByTestId('integration-row-ANTHROPIC_API_KEY')
    expect(within(anthropic).queryByText('••••••••')).not.toBeInTheDocument()
  })

  it('Replace sends the typed value to the API and refreshes the row', async () => {
    const user = userEvent.setup()
    const afterSave = sixItems().map((i) =>
      i.name === 'ANTHROPIC_API_KEY'
        ? { ...i, configured: true, source: 'db' as const, updated_by: 'mark@example.com' }
        : i
    )
    mockList
      .mockResolvedValueOnce(listResponse())
      .mockResolvedValue(listResponse({ items: afterSave }))
    mockSet.mockResolvedValue(afterSave[2])

    await renderLoaded()

    const row = screen.getByTestId('integration-row-ANTHROPIC_API_KEY')
    expect(within(row).getByText('Not configured')).toBeInTheDocument()

    await user.click(screen.getByTestId('integration-replace-ANTHROPIC_API_KEY'))

    const input = screen.getByTestId('integration-value-ANTHROPIC_API_KEY')
    // Write-only: the field must be a password input, never a text box that
    // shoulder-surfs the value being pasted in.
    expect(input).toHaveAttribute('type', 'password')

    await user.type(input, STORED_PLAINTEXT)
    await user.click(screen.getByTestId('integration-save-ANTHROPIC_API_KEY'))

    await waitFor(() => {
      expect(mockSet).toHaveBeenCalledWith('ANTHROPIC_API_KEY', STORED_PLAINTEXT)
    })

    // The row re-reads from the API and now reports itself configured; the
    // editor closes and the typed value is gone from the DOM.
    await waitFor(() => {
      const refreshed = screen.getByTestId('integration-row-ANTHROPIC_API_KEY')
      expect(within(refreshed).getByText('Configured')).toBeInTheDocument()
    })
    expect(screen.queryByTestId('integration-value-ANTHROPIC_API_KEY')).not.toBeInTheDocument()
    expect(document.body.textContent).not.toContain(STORED_PLAINTEXT)
    // Recent changes re-reads too, so the new entry appears without a reload.
    expect(mockAudit).toHaveBeenCalledTimes(2)
  })

  it('does not call the API when the value is empty', async () => {
    const user = userEvent.setup()
    await renderLoaded()

    await user.click(screen.getByTestId('integration-replace-ANTHROPIC_API_KEY'))
    await user.type(screen.getByTestId('integration-value-ANTHROPIC_API_KEY'), '   ')
    await user.click(screen.getByTestId('integration-save-ANTHROPIC_API_KEY'))

    expect(mockSet).not.toHaveBeenCalled()
    expect(screen.getByText('Enter a value before saving.')).toBeInTheDocument()
  })

  it('Clear removes an app-managed value after confirmation', async () => {
    const user = userEvent.setup()
    const afterClear = sixItems().map((i) =>
      i.name === 'RESEND_API_KEY' ? { ...i, configured: false, source: null } : i
    )
    mockList
      .mockResolvedValueOnce(listResponse())
      .mockResolvedValue(listResponse({ items: afterClear }))
    mockClear.mockResolvedValue(afterClear[1])

    await renderLoaded()

    await user.click(screen.getByTestId('integration-clear-RESEND_API_KEY'))
    // Destructive, so it asks first rather than firing on the first click.
    expect(mockClear).not.toHaveBeenCalled()

    await user.click(screen.getByTestId('integration-clear-confirm-RESEND_API_KEY'))

    await waitFor(() => {
      expect(mockClear).toHaveBeenCalledWith('RESEND_API_KEY')
    })
    await waitFor(() => {
      const row = screen.getByTestId('integration-row-RESEND_API_KEY')
      expect(within(row).getByText('Not configured')).toBeInTheDocument()
    })
  })

  it('discards a half-typed value when a Clear confirmation opens', async () => {
    const user = userEvent.setup()
    await renderLoaded()

    await user.click(screen.getByTestId('integration-replace-ANTHROPIC_API_KEY'))
    await user.type(screen.getByTestId('integration-value-ANTHROPIC_API_KEY'), STORED_PLAINTEXT)
    await user.click(screen.getByTestId('integration-clear-RESEND_API_KEY'))

    expect(screen.queryByTestId('integration-value-ANTHROPIC_API_KEY')).not.toBeInTheDocument()
    expect(document.body.textContent).not.toContain(STORED_PLAINTEXT)
  })

  it('warns when the encryption key is missing and says file/env values still work', async () => {
    mockList.mockResolvedValue(listResponse({ encryption_key_configured: false }))
    mockHealth.mockResolvedValue(healthResponse({ encryption_key_configured: false }))

    await renderLoaded()

    const banner = screen.getByTestId('integration-encryption-banner')
    expect(banner).toHaveTextContent('SCF_SECRET_KEY')
    expect(banner).toHaveTextContent(/still work/i)
  })

  it('hides the encryption banner when the key is configured', async () => {
    await renderLoaded()
    expect(screen.queryByTestId('integration-encryption-banner')).not.toBeInTheDocument()
  })

  it('locks Replace on operator-managed rows and explains why', async () => {
    await renderLoaded()

    const replace = screen.getByTestId('integration-replace-OIDC_CLIENT_SECRET')
    expect(replace).toBeDisabled()
    expect(replace).toHaveAttribute('title', expect.stringContaining('operator'))

    // App-managed rows stay editable.
    expect(screen.getByTestId('integration-replace-RESEND_API_KEY')).toBeEnabled()
  })

  it('renders the API detail message inline when a save is refused with 409', async () => {
    const user = userEvent.setup()
    mockSet.mockRejectedValue(
      apiError(409, 'SCF_SECRET_KEY is not configured — see docs')
    )

    await renderLoaded()

    await user.click(screen.getByTestId('integration-replace-ANTHROPIC_API_KEY'))
    await user.type(screen.getByTestId('integration-value-ANTHROPIC_API_KEY'), 'abc123')
    await user.click(screen.getByTestId('integration-save-ANTHROPIC_API_KEY'))

    await waitFor(() => {
      expect(
        screen.getByText('SCF_SECRET_KEY is not configured — see docs')
      ).toBeInTheDocument()
    })
    // The editor stays open so the operator can retry after fixing the cause.
    expect(screen.getByTestId('integration-value-ANTHROPIC_API_KEY')).toBeInTheDocument()
  })

  it('shows the setup-health split and what each unconfigured credential unlocks', async () => {
    await renderLoaded()

    const panel = await screen.findByTestId('integration-health-panel')
    expect(panel).toHaveTextContent('3 of 6 configured')
    expect(within(panel).getByText('Anthropic API key')).toBeInTheDocument()
    expect(
      within(panel).getByText('AI document generation and evidence assessment')
    ).toBeInTheDocument()
  })

  it('lists recent changes with action, credential, actor and time', async () => {
    await renderLoaded()

    const recent = await screen.findByTestId('integration-audit-list')
    expect(within(recent).getByText(/Replaced/)).toBeInTheDocument()
    expect(within(recent).getByText(/Resend API key/)).toBeInTheDocument()
    expect(within(recent).getByText(/mark@example\.com/)).toBeInTheDocument()
    expect(within(recent).getByText(/Cleared/)).toBeInTheDocument()
    expect(mockAudit).toHaveBeenCalledWith(10)
  })

  it('keeps the credential list usable when the audit endpoint fails', async () => {
    mockAudit.mockRejectedValue(new Error('audit unavailable'))

    await renderLoaded()

    expect(screen.getAllByTestId(/^integration-row-/)).toHaveLength(6)
    await waitFor(() => {
      expect(screen.getByText(/Recent changes are unavailable/i)).toBeInTheDocument()
    })
  })

  it('surfaces a load failure instead of rendering an empty inventory', async () => {
    mockList.mockRejectedValue(apiError(403, 'Platform admin access required'))

    render(<IntegrationsSettings />)

    await waitFor(() => {
      expect(screen.getByText('Platform admin access required')).toBeInTheDocument()
    })
    expect(screen.queryAllByTestId(/^integration-row-/)).toHaveLength(0)
  })
})
