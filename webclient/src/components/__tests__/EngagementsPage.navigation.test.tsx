// SPDX-License-Identifier: AGPL-3.0-only
/**
 * EngagementsPage: the scope and presentation drawers make every SCF control,
 * evidence object and artifact they show clickable.
 *
 * - A control opens in the Control Library — in the scoped list when it is in
 *   scope, in the full library when it is excluded or untracked.
 * - The presentation shows control → evidence object → artifacts, listing
 *   mapped evidence objects that have nothing collected yet.
 * - An evidence object opens in the Evidence workspace.
 * - An artifact opens the file through a freshly signed download link.
 */
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { ConfirmProvider } from '../../__tests__/confirmDialog'
import EngagementsPage from '../EngagementsPage'
import {
  getEngagementPresentation,
  getEngagementScope,
  getEvidenceFile,
  listEngagements,
  type AuditEngagement,
  type FrameworkPresentation,
} from '../../data/apiClient'
import { fetchFrameworks } from '../../data/catalogApi'

vi.mock('../../data/apiClient', () => ({
  listEngagements: vi.fn(),
  createEngagement: vi.fn(),
  deleteEngagement: vi.fn(),
  getEngagementScope: vi.fn(),
  getEngagementPresentation: vi.fn(),
  listEngagementAuditors: vi.fn(),
  grantEngagementAuditor: vi.fn(),
  revokeEngagementAuditor: vi.fn(),
  listEngagementQueries: vi.fn(),
  createEngagementQuery: vi.fn(),
  getEngagementQuery: vi.fn(),
  respondToEngagementQuery: vi.fn(),
  updateEngagementQueryStatus: vi.fn(),
  getEvidenceFile: vi.fn(),
}))

vi.mock('../../data/catalogApi', () => ({
  fetchFrameworks: vi.fn(),
}))

const ORG_ID = 'org-1'

const ENGAGEMENT: AuditEngagement = {
  id: 'eng-1',
  organization_id: ORG_ID,
  name: 'ISO surveillance 2026',
  frameworks: ['iso_27001_2022'],
  status: 'active',
  start_date: '2026-01-01',
  end_date: '2026-12-31',
  created_by_user_id: null,
  created_at: '2026-01-01T00:00:00Z',
  updated_at: '2026-01-01T00:00:00Z',
  scope_count: 2,
}

const PRESENTATION: FrameworkPresentation = {
  framework: 'iso_27001_2022',
  start_date: '2026-01-01',
  end_date: '2026-12-31',
  clauses: [{
    clause_id: '5.1',
    controls: [{
      scf_id: 'GOV-01',
      control_name: 'Cybersecurity governance program',
      scope_status: 'in_scope',
      out_of_scope_justification: null,
      source_frameworks: ['iso_27001_2022'],
      scoped_control_id: 'sc-1',
      implementation_status: 'Implemented',
      maturity_level: null,
      owner: null,
      evidence: [{
        id: 'file-1', evidence_id: 'E-GOV-01', filename: 'policy.pdf',
        uploaded_at: '2026-03-01T00:00:00Z', review_status: null, in_window: true,
      }],
      evidence_in_window_count: 1,
      evidence_objects: [
        {
          evidence_id: 'E-GOV-01', artifact_title: 'Security policy', area_of_focus: 'Governance',
          in_window_count: 1,
          artifacts: [{
            id: 'file-1', evidence_id: 'E-GOV-01', filename: 'policy.pdf',
            uploaded_at: '2026-03-01T00:00:00Z', review_status: null, in_window: true,
          }],
        },
        {
          evidence_id: 'E-GOV-02', artifact_title: 'Program charter', area_of_focus: 'Governance',
          in_window_count: 0, artifacts: [],
        },
      ],
      queries: [],
    }],
  }],
} as unknown as FrameworkPresentation

async function openPresentation(props: Partial<React.ComponentProps<typeof EngagementsPage>> = {}) {
  render(<EngagementsPage organizationId={ORG_ID} {...props} />, { wrapper: ConfirmProvider })
  await userEvent.click(await screen.findByRole('button', { name: 'Present' }))
  return screen.findByRole('dialog')
}

beforeEach(() => {
  vi.mocked(fetchFrameworks).mockResolvedValue([])
  vi.mocked(listEngagements).mockResolvedValue([ENGAGEMENT])
  vi.mocked(getEngagementPresentation).mockResolvedValue(PRESENTATION)
})

afterEach(() => {
  vi.clearAllMocks()
  vi.restoreAllMocks()
})

describe('EngagementsPage presentation drawer', () => {
  it('shows each evidence object under the control, with its artifacts nested under it', async () => {
    const dialog = await openPresentation({ onNavigateToControl: vi.fn(), onNavigateToEvidence: vi.fn() })
    const govObject = await within(dialog).findByRole('button', { name: 'E-GOV-01' })
    const objectRow = govObject.closest('li')!
    expect(within(objectRow).getByText('Security policy')).toBeInTheDocument()
    expect(within(objectRow).getByRole('button', { name: /policy\.pdf/ })).toBeInTheDocument()

    // A mapped object with nothing collected is still listed, marked as such.
    const emptyRow = within(dialog).getByRole('button', { name: 'E-GOV-02' }).closest('li')!
    expect(within(emptyRow).getByText('Not collected')).toBeInTheDocument()
    expect(within(dialog).getByText(/1 of 2 objects collected/)).toBeInTheDocument()
  })

  it('shows ids as plain text when the page is given nowhere to navigate', async () => {
    const dialog = await openPresentation()
    expect(await within(dialog).findByText('E-GOV-01')).not.toHaveAttribute('type', 'button')
    expect(within(dialog).queryByRole('button', { name: 'GOV-01' })).toBeNull()
    expect(within(dialog).queryByRole('button', { name: 'E-GOV-01' })).toBeNull()
  })

  it('opens an in-scope control in the scoped Control Library', async () => {
    const onNavigateToControl = vi.fn()
    const dialog = await openPresentation({ onNavigateToControl })
    await userEvent.click(await within(dialog).findByRole('button', { name: 'GOV-01' }))
    expect(onNavigateToControl).toHaveBeenCalledWith('GOV-01', 'in-scope')
  })

  it('opens an evidence object in the Evidence workspace', async () => {
    const onNavigateToEvidence = vi.fn()
    const dialog = await openPresentation({ onNavigateToEvidence })
    await userEvent.click(await within(dialog).findByRole('button', { name: 'E-GOV-02' }))
    expect(onNavigateToEvidence).toHaveBeenCalledWith('E-GOV-02')
  })

  it('opens an artifact through a freshly signed download link', async () => {
    const tab = { opener: {}, location: { href: '' }, close: vi.fn() }
    const open = vi.spyOn(window, 'open').mockReturnValue(tab as unknown as Window)
    vi.mocked(getEvidenceFile).mockResolvedValue({
      download_url: '/api/organizations/org-1/evidence/E-GOV-01/files/file-1/download?token=t&expires=1',
    } as Awaited<ReturnType<typeof getEvidenceFile>>)

    const dialog = await openPresentation()
    await userEvent.click(await within(dialog).findByRole('button', { name: /policy\.pdf/ }))

    expect(open).toHaveBeenCalledWith('about:blank', '_blank')
    expect(getEvidenceFile).toHaveBeenCalledWith('E-GOV-01', 'file-1', ORG_ID)
    await waitFor(() => expect(tab.location.href).toContain('/files/file-1/download?token=t'))
    expect(tab.opener).toBeNull()
  })

  it('closes the blank tab and says so when the file cannot be opened', async () => {
    const tab = { opener: {}, location: { href: '' }, close: vi.fn() }
    vi.spyOn(window, 'open').mockReturnValue(tab as unknown as Window)
    vi.mocked(getEvidenceFile).mockRejectedValue(new Error('Evidence file not found'))

    const dialog = await openPresentation()
    await userEvent.click(await within(dialog).findByRole('button', { name: /policy\.pdf/ }))

    expect(await within(dialog).findByRole('alert')).toHaveTextContent('Could not open policy.pdf: Evidence file not found')
    expect(tab.close).toHaveBeenCalled()
  })
})

describe('EngagementsPage scope drawer', () => {
  it('opens an excluded control in the full library, where it can be found', async () => {
    vi.mocked(getEngagementScope).mockResolvedValue([{
      id: 's-1', scoped_control_id: 'sc-9', scf_id: 'PRI-01', control_name: 'Privacy program',
      scope_status: 'excluded', out_of_scope_justification: 'No personal data processed',
      source_frameworks: ['iso_27001_2022'], added_at: '2026-01-01T00:00:00Z',
    }])
    const onNavigateToControl = vi.fn()
    render(<EngagementsPage organizationId={ORG_ID} onNavigateToControl={onNavigateToControl} />, { wrapper: ConfirmProvider })
    await userEvent.click(await screen.findByRole('button', { name: 'View scope' }))
    const dialog = await screen.findByRole('dialog')
    await userEvent.click(await within(dialog).findByRole('button', { name: 'PRI-01' }))
    expect(onNavigateToControl).toHaveBeenCalledWith('PRI-01', 'full-library')
  })
})
