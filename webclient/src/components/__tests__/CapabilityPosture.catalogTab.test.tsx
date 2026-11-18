// SPDX-License-Identifier: AGPL-3.0-only
/**
 * Capability Posture — the theme detail's Catalog tab.
 *
 * The controls route lists in-scope controls only unless asked for `all`.
 * The detail screen never asked, so its Catalog tab repeated the In Scope
 * tab. These tests pin that the detail reads every control of the theme,
 * across pages.
 */
import { render, screen, fireEvent, within } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import CapabilityPosture from '../CapabilityPosture'
import {
  getCapabilityThemes,
  getCapabilityThemeEvidencePosture,
  fetchScopedControlStats,
  getCapabilityThemeControls,
  getEvidenceHealth,
} from '../../data/apiClient'
import type { CapabilityThemeControlItem, CapabilityThemeResponse } from '../../types'

vi.mock('../../data/apiClient', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../data/apiClient')>()
  return {
    ...actual,
    getCapabilityThemes: vi.fn(),
    getCapabilityThemeEvidencePosture: vi.fn(),
    fetchScopedControlStats: vi.fn(),
    getCapabilityThemeControls: vi.fn(),
    getEvidenceHealth: vi.fn(),
  }
})

const THEME: CapabilityThemeResponse = {
  theme_code: 'CM',
  name: 'Change Management',
  description: 'Catalog tab test theme.',
  ksi_reference: 'KSI-CM',
  icon: null,
  display_order: 1,
  total_controls: 3,
  scoped_controls: 1,
  posture: {
    monitored: 0, implemented: 1, ready_for_review: 0, in_progress: 0,
    not_started: 0, at_risk: 0, not_applicable: 0, deferred: 0,
  },
  maturity_score: 3,
  implementation_coverage: 1,
  implementation_band: 'Strong',
  maturity_band: 'Moderate',
  evidence_coverage: 1,
  evidence_coverage_band: 'Strong',
  evidence_quality: 0.5,
  evidence_quality_band: 'Moderate',
  evidence_quality_warning: null,
  composite_score: 0.7,
  composite_band: 'Moderate',
}

function control(scf_id: string, selected: boolean): CapabilityThemeControlItem {
  return {
    scf_id,
    control_name: `Control ${scf_id}`,
    scf_domain: 'Change Management',
    selected,
    implementation_status: selected ? 'implemented' : null,
    maturity_level: selected ? 'L3' : null,
    relevance: 'primary',
  }
}

const ALL = [control('CHG-02', true), control('CHG-03', false), control('CHG-04', false)]

beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(getCapabilityThemes).mockResolvedValue({ themes: [THEME] })
  vi.mocked(getCapabilityThemeEvidencePosture).mockResolvedValue({ themes: [] })
  vi.mocked(fetchScopedControlStats).mockResolvedValue(
    { total_controls: 10, in_scope: 1 } as unknown as Awaited<ReturnType<typeof fetchScopedControlStats>>
  )
  vi.mocked(getEvidenceHealth).mockResolvedValue(
    { items: [] } as unknown as Awaited<ReturnType<typeof getEvidenceHealth>>
  )
  // A server that answers like the real one: in-scope only unless asked for
  // `all`, and two controls a page however many were asked for.
  vi.mocked(getCapabilityThemeControls).mockImplementation(async (_code, params) => {
    const pool = params?.scope_status === 'all' ? ALL : ALL.filter(c => c.selected)
    const offset = params?.offset ?? 0
    return {
      theme_code: 'CM',
      theme_name: 'Change Management',
      controls: pool.slice(offset, offset + 2),
      total: pool.length,
      offset,
      limit: 2,
    }
  })
})

async function openDetail() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } })
  render(
    <QueryClientProvider client={client}>
      <CapabilityPosture organizationId="org-catalog-tab" />
    </QueryClientProvider>
  )
  const card = await screen.findByText('Change Management')
  fireEvent.click(card.closest('button')!)
  await screen.findByText('Back to Themes')
  await screen.findByText('Control CHG-02')
}

function tab(name: RegExp) {
  return screen.getByRole('tab', { name })
}

describe('theme detail — Catalog tab', () => {
  it('counts every control of the theme, not only the scoped ones', async () => {
    await openDetail()
    expect(tab(/In Scope/).textContent).toMatch(/1$/)
    expect(tab(/Catalog/).textContent).toMatch(/3$/)
  })

  it('lists the unscoped controls on the Catalog tab and not on In Scope', async () => {
    await openDetail()
    expect(screen.queryByText('Control CHG-03')).not.toBeInTheDocument()
    fireEvent.click(tab(/Catalog/))
    const table = await screen.findByRole('table')
    expect(within(table).getByText('Control CHG-02')).toBeInTheDocument()
    expect(within(table).getByText('Control CHG-03')).toBeInTheDocument()
    expect(within(table).getByText('Control CHG-04')).toBeInTheDocument()
  })

  it('asks the server for all controls and follows the pages to the end', async () => {
    await openDetail()
    const calls = vi.mocked(getCapabilityThemeControls).mock.calls.map(c => c[1])
    expect(calls.every(p => p?.scope_status === 'all')).toBe(true)
    expect(calls.map(p => p?.offset)).toEqual([0, 2])
  })

  it('stops when the server sends an empty page before the total is reached', async () => {
    vi.mocked(getCapabilityThemeControls).mockImplementation(async (_code, params) => ({
      theme_code: 'CM',
      theme_name: 'Change Management',
      controls: (params?.offset ?? 0) === 0 ? [ALL[0]] : [],
      total: 99,
      offset: params?.offset ?? 0,
      limit: 200,
    }))
    await openDetail()
    expect(vi.mocked(getCapabilityThemeControls).mock.calls.length).toBe(2)
    expect(tab(/Catalog/).textContent).toMatch(/1$/)
  })
})

describe('theme detail — status pill', () => {
  it('hands the status colour to the stylesheet instead of painting it inline', async () => {
    await openDetail()
    const pill = within(screen.getByRole('table')).getByText('Implemented')
    expect(pill).toHaveClass('cp-status-badge')
    expect(pill.style.getPropertyValue('--cp-status-color')).toBe('var(--success)')
    expect(pill.style.backgroundColor).toBe('')
  })
})
