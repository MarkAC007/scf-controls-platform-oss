/**
 * AssessmentEngineSettings — Settings → AI assessment engine.
 *
 * What these tests defend:
 *
 *   - the saved engine is the one shown selected, and an absent field reads as
 *     Claude, the backend's default;
 *   - a Jev mode without a TypeSafe key is called out before and after saving,
 *     because the backend will record an error for every assessment until the
 *     key exists — silently, from the user's side;
 *   - the shadow comparison only claims numbers it has. Zero comparisons is a
 *     different state from 0% agreement and must read as one;
 *   - saving sends only the engine, and only an org admin is offered the save
 *     (the PATCH requires admin).
 */
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import AssessmentEngineSettings from '../AssessmentEngineSettings'
import { getAssessmentEngineStatus } from '../../data/assessmentEngineApi'
import type { AssessmentEngineStatus, ShadowStats } from '../../data/assessmentEngineApi'
import { updateOrganizationSettings } from '../../data/apiClient'

vi.mock('../../data/assessmentEngineApi', () => ({
  getAssessmentEngineStatus: vi.fn(),
}))

vi.mock('../../data/apiClient', () => ({
  updateOrganizationSettings: vi.fn(),
}))

const mockRefetch = vi.fn()
let settings: Record<string, unknown> | undefined
vi.mock('../../hooks/useOrganizationSettings', () => ({
  useOrganizationSettings: () => ({ data: settings, refetch: mockRefetch }),
}))

// useIsOrgAdmin reads AuthContext; without this the render dies with
// "useAuth must be used within AuthProvider" rather than testing anything.
let admin = true
vi.mock('../../hooks/useIsOrgAdmin', () => ({
  useIsOrgAdmin: () => admin,
}))

vi.mock('react-hot-toast', () => ({
  toast: { success: vi.fn(), error: vi.fn() },
}))

const mockStatus = vi.mocked(getAssessmentEngineStatus)
const mockUpdate = vi.mocked(updateOrganizationSettings)

const ORG = 'org-1'

function stats(overrides: Partial<ShadowStats> = {}): ShadowStats {
  return {
    compared_verdicts: 0,
    failed_verdicts: 0,
    objectives_compared: 0,
    objectives_agreed: 0,
    agreement_rate: null,
    confident_objectives: 0,
    confident_agreed: 0,
    confident_agreement_rate: null,
    status_agreement_rate: null,
    mean_latency_ms: null,
    total_cost_cents: null,
    last_compared_at: null,
    ...overrides,
  }
}

function status(overrides: Partial<AssessmentEngineStatus> = {}): AssessmentEngineStatus {
  return {
    engine: 'llm',
    typesafe_key_configured: true,
    jev_model_id: 'jev-1.13.0',
    confidence_cutoff: 0.85,
    shadow_stats: stats(),
    ...overrides,
  }
}

beforeEach(() => {
  vi.clearAllMocks()
  settings = { evidence_assessment_engine: 'llm' }
  admin = true
  mockStatus.mockResolvedValue(status())
  mockUpdate.mockResolvedValue({} as never)
  mockRefetch.mockResolvedValue({})
})

describe('AssessmentEngineSettings', () => {
  it('renders the three engines with the saved one selected', async () => {
    settings = { evidence_assessment_engine: 'jev_shadow' }
    render(<AssessmentEngineSettings organizationId={ORG} />)

    expect(await screen.findByRole('heading', { name: 'AI assessment engine' })).toBeInTheDocument()
    const radios = screen.getAllByRole('radio')
    expect(radios).toHaveLength(3)
    expect(screen.getByRole('radio', { name: /Claude with Jev shadow/ })).toBeChecked()
    expect(screen.getByRole('radio', { name: /Claude \(LLM\)/ })).not.toBeChecked()
    expect(screen.getByRole('radio', { name: /Jev \(System One\)/ })).not.toBeChecked()
    expect(mockStatus).toHaveBeenCalledWith(ORG)
  })

  it('reads an absent engine field as Claude', async () => {
    settings = {}
    render(<AssessmentEngineSettings organizationId={ORG} />)
    expect(await screen.findByRole('radio', { name: /Claude \(LLM\)/ })).toBeChecked()
  })

  it('warns about the missing TypeSafe key once a Jev mode is selected', async () => {
    mockStatus.mockResolvedValue(status({ typesafe_key_configured: false }))
    const user = userEvent.setup()
    render(<AssessmentEngineSettings organizationId={ORG} />)

    await screen.findByRole('radio', { name: /Claude \(LLM\)/ })
    await waitFor(() => expect(mockStatus).toHaveBeenCalled())
    expect(screen.queryByTestId('assessment-engine-key-warning')).not.toBeInTheDocument()

    await user.click(screen.getByRole('radio', { name: /Jev \(System One\)/ }))
    expect(await screen.findByTestId('assessment-engine-key-warning')).toHaveTextContent(
      'TypeSafe API key is not configured.',
    )
  })

  it('warns about the missing key when a Jev mode is already saved', async () => {
    settings = { evidence_assessment_engine: 'jev_shadow' }
    mockStatus.mockResolvedValue(status({ engine: 'jev_shadow', typesafe_key_configured: false }))
    render(<AssessmentEngineSettings organizationId={ORG} />)
    expect(await screen.findByTestId('assessment-engine-key-warning')).toBeInTheDocument()
  })

  it('does not warn when the key is configured', async () => {
    settings = { evidence_assessment_engine: 'jev' }
    render(<AssessmentEngineSettings organizationId={ORG} />)
    await waitFor(() => expect(mockStatus).toHaveBeenCalled())
    await screen.findByText(/No shadow comparisons yet/)
    expect(screen.queryByTestId('assessment-engine-key-warning')).not.toBeInTheDocument()
  })

  it('shows the shadow comparison when verdicts have been compared', async () => {
    mockStatus.mockResolvedValue(
      status({
        engine: 'jev_shadow',
        shadow_stats: stats({
          compared_verdicts: 12,
          failed_verdicts: 1,
          objectives_compared: 40,
          objectives_agreed: 34,
          agreement_rate: 0.85,
          confident_objectives: 30,
          confident_agreed: 29,
          confident_agreement_rate: 29 / 30,
          status_agreement_rate: 0.75,
          mean_latency_ms: 640.4,
          total_cost_cents: 0.42,
          last_compared_at: '2026-09-29T06:00:00',
        }),
      }),
    )
    render(<AssessmentEngineSettings organizationId={ORG} />)

    const block = await screen.findByTestId('assessment-engine-shadow-stats')
    expect(block).toHaveTextContent('12')
    expect(block).toHaveTextContent('34/40')
    expect(block).toHaveTextContent('85%')
    expect(block).toHaveTextContent('29/30')
    expect(block).toHaveTextContent('97%')
    expect(block).toHaveTextContent('75%')
    expect(block).toHaveTextContent('640 ms')
    expect(block).toHaveTextContent('0.42¢')
    expect(screen.queryByText(/No shadow comparisons yet/)).not.toBeInTheDocument()
  })

  it('says there is nothing to compare rather than showing zeros', async () => {
    render(<AssessmentEngineSettings organizationId={ORG} />)
    expect(await screen.findByText(/No shadow comparisons yet/)).toBeInTheDocument()
    expect(screen.queryByTestId('assessment-engine-shadow-stats')).not.toBeInTheDocument()
  })

  it('saves only the chosen engine, then refetches', async () => {
    const user = userEvent.setup()
    render(<AssessmentEngineSettings organizationId={ORG} />)

    const save = await screen.findByRole('button', { name: 'Save engine' })
    expect(save).toBeDisabled() // nothing changed yet

    await user.click(screen.getByRole('radio', { name: /Claude with Jev shadow/ }))
    expect(save).toBeEnabled()
    await user.click(save)

    await waitFor(() =>
      expect(mockUpdate).toHaveBeenCalledWith(ORG, { evidence_assessment_engine: 'jev_shadow' }),
    )
    await waitFor(() => expect(mockRefetch).toHaveBeenCalled())
    // The status is re-read too: the engine it reports has just changed.
    await waitFor(() => expect(mockStatus).toHaveBeenCalledTimes(2))
  })

  it('is read-only below org admin', async () => {
    admin = false
    render(<AssessmentEngineSettings organizationId={ORG} />)

    await screen.findByRole('radio', { name: /Claude \(LLM\)/ })
    screen.getAllByRole('radio').forEach(r => expect(r).toBeDisabled())
    expect(screen.queryByRole('button', { name: 'Save engine' })).not.toBeInTheDocument()
    expect(screen.getByText(/organisation administrator/)).toBeInTheDocument()
  })
})
