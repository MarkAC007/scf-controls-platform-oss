/**
 * Pinning suite for Task 7 — Consultant Portal restyle.
 *
 * Covers: stats row renders correct values; grid⇄comparison toggle;
 * sort select fires; invite modal opens on button click;
 * cancel-invite fires the callback; ClientCard renders awaiting-admin badge.
 */
import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import ConsultantDashboard from '../ConsultantDashboard'
import ClientCard from '../ClientCard'
import InviteClientModal from '../InviteClientModal'
import type { ClientSummary, ConsultantInvite } from '../../../types'

// ── Fixtures ─────────────────────────────────────────────────────────────────

function makeClient(overrides: Partial<ClientSummary> = {}): ClientSummary {
  return {
    organization_id: 'org-1',
    organization_name: 'Odin Vision',
    awaiting_admin: false,
    framework_readiness_percent: 78,
    controls_implemented: 214,
    controls_total: 288,
    controls_in_progress: 41,
    controls_at_risk: 0,
    evidence_tracked: 142,
    evidence_total: 198,
    last_activity_date: new Date().toISOString(),
    last_activity_by: 'Mark',
    primary_framework: 'ISO 27001',
    ...overrides,
  }
}

const baseClients: ClientSummary[] = [
  makeClient({ organization_id: 'org-1', organization_name: 'Odin Vision', framework_readiness_percent: 78 }),
  makeClient({
    organization_id: 'org-2',
    organization_name: 'Compliance Genie',
    framework_readiness_percent: 64,
    controls_implemented: 19,
    controls_total: 83,
    controls_in_progress: 62,
    controls_at_risk: 0,
    evidence_tracked: 19,
    evidence_total: 61,
    last_activity_date: new Date(Date.now() - 86_400_000).toISOString(),
  }),
  makeClient({
    organization_id: 'org-3',
    organization_name: 'UnitSix8',
    framework_readiness_percent: 41,
    controls_implemented: 37,
    controls_total: 121,
    controls_in_progress: 28,
    controls_at_risk: 3,
    evidence_tracked: 12,
    evidence_total: 64,
    awaiting_admin: true,
    last_activity_date: new Date(Date.now() - 14 * 86_400_000).toISOString(),
  }),
]

// ── Stats row ─────────────────────────────────────────────────────────────────

describe('ConsultantDashboard — stats strip', () => {
  it('renders total client count', () => {
    render(<ConsultantDashboard clients={baseClients} />)
    // stats strip shows "3" under "Total Clients"
    expect(screen.getByText('3')).toBeInTheDocument()
  })

  it('renders avg readiness', () => {
    render(<ConsultantDashboard clients={baseClients} />)
    const avgReadiness = Math.round((78 + 64 + 41) / 3)
    expect(screen.getByText(`${avgReadiness}%`)).toBeInTheDocument()
  })

  it('renders clients-with-risks tile when risk count > 0', () => {
    render(<ConsultantDashboard clients={baseClients} />)
    expect(screen.getByText('Clients with Risks')).toBeInTheDocument()
  })

  it('renders awaiting-admin tile when count > 0', () => {
    render(<ConsultantDashboard clients={baseClients} />)
    expect(screen.getByText('Awaiting Admin')).toBeInTheDocument()
  })

  it('omits awaiting-admin tile when no clients await admin', () => {
    const noWaiting = baseClients.map(c => ({ ...c, awaiting_admin: false }))
    render(<ConsultantDashboard clients={noWaiting} />)
    expect(screen.queryByText('Awaiting Admin')).toBeNull()
  })
})

// ── Grid ⇄ Comparison toggle ──────────────────────────────────────────────────

describe('ConsultantDashboard — view toggle', () => {
  it('starts in grid view: shows client cards', () => {
    render(<ConsultantDashboard clients={baseClients} />)
    // In grid view each ClientCard renders the org name
    expect(screen.getByText('Odin Vision')).toBeInTheDocument()
  })

  it('switches to comparison view when Comparison button pressed', () => {
    render(<ConsultantDashboard clients={baseClients} />)
    const compBtn = screen.getByRole('button', { name: /comparison/i })
    fireEvent.click(compBtn)
    // CrossOrgComparison renders "Cross-Org Comparison" heading
    expect(screen.getByText(/cross-org comparison/i)).toBeInTheDocument()
  })

  it('switches back to grid view when Grid button pressed', () => {
    render(<ConsultantDashboard clients={baseClients} />)
    fireEvent.click(screen.getByRole('button', { name: /comparison/i }))
    fireEvent.click(screen.getByRole('button', { name: /^grid$/i }))
    // Client cards are back
    expect(screen.getAllByText('Odin Vision').length).toBeGreaterThan(0)
  })
})

// ── Sort select ───────────────────────────────────────────────────────────────

describe('ConsultantDashboard — sort select', () => {
  it('renders the sort select with the three options', () => {
    render(<ConsultantDashboard clients={baseClients} />)
    const select = screen.getByRole('combobox', { name: /sort clients by/i })
    expect(select).toBeInTheDocument()
    const options = Array.from((select as HTMLSelectElement).options).map(o => o.value)
    expect(options).toContain('name')
    expect(options).toContain('readiness')
    expect(options).toContain('activity')
  })

  it('changing the sort select to name reorders the list', () => {
    render(<ConsultantDashboard clients={baseClients} />)
    const select = screen.getByRole('combobox', { name: /sort clients by/i })
    fireEvent.change(select, { target: { value: 'name' } })
    // After sort by name "Compliance Genie" should appear before "Odin Vision"
    const cards = screen.getAllByText(/compliance genie|odin vision|unitsix8/i)
    const names = cards.map(el => el.textContent)
    expect(names[0]).toMatch(/compliance genie/i)
  })
})

// ── Invite modal ──────────────────────────────────────────────────────────────

describe('ConsultantDashboard — invite modal', () => {
  it('opens the invite modal when "Invite Client" is clicked', () => {
    render(
      <ConsultantDashboard
        clients={baseClients}
        onCreateOrg={vi.fn()}
        onInviteAdmin={vi.fn()}
      />
    )
    fireEvent.click(screen.getByRole('button', { name: /\+ invite client/i }))
    expect(screen.getByText('Invite Client')).toBeInTheDocument()
  })
})

// ── Cancel invite ─────────────────────────────────────────────────────────────

describe('InviteClientModal — cancel invite', () => {
  const pendingInvite: ConsultantInvite = {
    id: 'inv-1',
    email: 'admin@client.com',
    organization_name: 'Test Org',
    organization_id: 'org-99',
    invited_by_email: 'consultant@firm.com',
    status: 'pending',
    created_at: new Date().toISOString(),
    expires_at: new Date(Date.now() + 7 * 86_400_000).toISOString(),
  }

  it('fires onCancelInvite with the invite id when cancel is clicked', () => {
    const onCancelInvite = vi.fn()
    const onClose = vi.fn()
    render(
      <InviteClientModal
        pendingInvites={[pendingInvite]}
        onClose={onClose}
        onSubmit={vi.fn(async () => {})}
        onCancelInvite={onCancelInvite}
      />
    )
    const cancelBtn = screen.getByTitle('Cancel invitation')
    fireEvent.click(cancelBtn)
    expect(onCancelInvite).toHaveBeenCalledWith('inv-1')
  })
})

// ── ClientCard — awaiting admin badge ─────────────────────────────────────────

describe('ClientCard', () => {
  it('shows awaiting-admin badge when awaiting_admin is true', () => {
    const client = makeClient({ awaiting_admin: true, organization_id: 'org-aw' })
    render(<ClientCard client={client} />)
    expect(screen.getByText('Awaiting admin')).toBeInTheDocument()
  })

  it('does not show awaiting-admin badge when awaiting_admin is false', () => {
    const client = makeClient({ awaiting_admin: false })
    render(<ClientCard client={client} />)
    expect(screen.queryByText('Awaiting admin')).toBeNull()
  })

  it('shows "Current" badge for current org', () => {
    const client = makeClient({ organization_id: 'org-cur' })
    render(<ClientCard client={client} isCurrentOrg />)
    expect(screen.getByText('Current')).toBeInTheDocument()
  })

  it('renders the framework badge when not current org', () => {
    const client = makeClient({ primary_framework: 'ISO 27001' })
    render(<ClientCard client={client} isCurrentOrg={false} />)
    expect(screen.getByText('ISO 27001')).toBeInTheDocument()
  })

  it('renders readiness percentage', () => {
    const client = makeClient({ framework_readiness_percent: 78 })
    render(<ClientCard client={client} />)
    expect(screen.getByText('78%')).toBeInTheDocument()
  })
})
