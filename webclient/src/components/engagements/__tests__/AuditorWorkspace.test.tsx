// SPDX-License-Identifier: AGPL-3.0-only
/**
 * AuditorWorkspace — what a signed-in person with no organisation sees.
 *
 * An external auditor is not a member of the organisation they audit. Before
 * this screen they landed on "No organisation yet" with no way to reach the
 * engagement they had been granted and no notification bell.
 */
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import AuditorWorkspace from '../AuditorWorkspace'
import { listMyEngagements, type AuditEngagement } from '../../../data/apiClient'
import { fetchFrameworks } from '../../../data/catalogApi'

const logout = vi.fn()
let bellProps: { onNavigateToEngagementReview?: (t: unknown) => void } = {}

vi.mock('../../../data/apiClient', () => ({ listMyEngagements: vi.fn() }))
vi.mock('../../../data/catalogApi', () => ({ fetchFrameworks: vi.fn() }))
vi.mock('../../../contexts/AuthContext', () => ({
  useAuth: () => ({ user: { email: 'auditor@firm.example' }, logout }),
}))
vi.mock('../../../contexts/OrganizationContext', () => ({
  useOrganization: () => ({ refreshOrganizations: vi.fn() }),
}))
vi.mock('../../ThemeMenu', () => ({ default: () => <div data-testid="theme-menu" /> }))
vi.mock('../../NotificationBell', () => ({
  NotificationBell: (props: typeof bellProps) => { bellProps = props; return <div data-testid="bell" /> },
}))
vi.mock('../EngagementReviewWorkspace', () => ({
  default: (props: { organizationId: string; engagement: AuditEngagement; initialFocus: unknown; onBack: () => void }) => (
    <div data-testid="workspace">
      {props.organizationId}|{props.engagement.id}|{JSON.stringify(props.initialFocus)}
      <button type="button" onClick={props.onBack}>back</button>
    </div>
  ),
}))

const ENGAGEMENT: AuditEngagement = {
  id: 'eng-1', organization_id: 'org-9', name: 'Cyber Essentials 2026', frameworks: ['cyber_essentials'],
  status: 'active', start_date: '2026-01-01', end_date: '2026-12-31', created_by_user_id: null,
  created_at: '2026-01-01T00:00:00Z', updated_at: '2026-01-01T00:00:00Z', scope_count: 40,
}

beforeEach(() => {
  vi.clearAllMocks()
  bellProps = {}
  vi.mocked(fetchFrameworks).mockResolvedValue([{ id: 'cyber_essentials', name: 'Cyber Essentials' }] as never)
})

describe('AuditorWorkspace', () => {
  it('falls back to the no-organisation screen when the person audits nothing', async () => {
    vi.mocked(listMyEngagements).mockResolvedValue([])
    render(<AuditorWorkspace />)
    expect(await screen.findByText('No organisation yet')).toBeInTheDocument()
    expect(screen.queryByTestId('bell')).not.toBeInTheDocument()
  })

  it('lists the engagements with the bell and sign out', async () => {
    vi.mocked(listMyEngagements).mockResolvedValue([ENGAGEMENT])
    render(<AuditorWorkspace />)
    expect(await screen.findByRole('heading', { name: 'Cyber Essentials 2026' })).toBeInTheDocument()
    await waitFor(() => expect(screen.getByText(/Cyber Essentials · Audit window/)).toBeInTheDocument())
    expect(screen.getByTestId('bell')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Sign out' }))
    expect(logout).toHaveBeenCalledTimes(1)
  })

  it('opens an engagement in its own organisation and comes back to the list', async () => {
    vi.mocked(listMyEngagements).mockResolvedValue([ENGAGEMENT])
    render(<AuditorWorkspace />)
    await userEvent.click(await screen.findByRole('button', { name: 'Open review board' }))
    expect(screen.getByTestId('workspace')).toHaveTextContent('org-9|eng-1|null')
    await userEvent.click(screen.getByRole('button', { name: 'back' }))
    expect(await screen.findByRole('heading', { name: 'My engagements' })).toBeInTheDocument()
    expect(listMyEngagements).toHaveBeenCalledTimes(2)
  })

  it('opens the card a notification names', async () => {
    vi.mocked(listMyEngagements).mockResolvedValue([ENGAGEMENT])
    render(<AuditorWorkspace />)
    await screen.findByRole('heading', { name: 'Cyber Essentials 2026' })
    bellProps.onNavigateToEngagementReview?.({ organizationId: 'org-9', engagementId: 'eng-1', focus: { reviewId: 'r-1' } })
    expect(await screen.findByTestId('workspace')).toHaveTextContent('{"reviewId":"r-1"}')
  })

  it('says so when a notification names an engagement the person can no longer reach', async () => {
    vi.mocked(listMyEngagements).mockResolvedValue([ENGAGEMENT])
    render(<AuditorWorkspace />)
    await screen.findByRole('heading', { name: 'Cyber Essentials 2026' })
    bellProps.onNavigateToEngagementReview?.({ organizationId: 'org-9', engagementId: 'gone' })
    expect(await screen.findByText(/no longer in your list/)).toBeInTheDocument()
    expect(screen.queryByTestId('workspace')).not.toBeInTheDocument()
  })

  it('shows the error and a retry when the list cannot be loaded', async () => {
    vi.mocked(listMyEngagements).mockRejectedValueOnce(new Error('Server unavailable')).mockResolvedValue([ENGAGEMENT])
    render(<AuditorWorkspace />)
    expect(await screen.findByRole('alert')).toHaveTextContent('Server unavailable')
    await userEvent.click(screen.getByRole('button', { name: 'Try again' }))
    expect(await screen.findByRole('heading', { name: 'Cyber Essentials 2026' })).toBeInTheDocument()
  })
})
