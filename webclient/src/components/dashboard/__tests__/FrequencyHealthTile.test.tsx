/**
 * FrequencyHealthTile — component tests (M4 #574 PR3, ISC-39 acceptance).
 *
 * Covers:
 *   - loading skeleton renders
 *   - error state renders with retry button on fetch failure
 *   - empty state renders when misaligned_count is 0
 *   - happy path renders count, expand toggle, and per-evidence rows
 *   - clicking Apply fix calls createOrUpdateEvidenceTracking with the right args
 *   - apply error path renders error message
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor, fireEvent, cleanup } from '@testing-library/react'
import { FrequencyHealthTile } from '../FrequencyHealthTile'
import type {
  FrequencyHealthResponse,
  FrequencyHealthItem,
} from '../../../types'

// Mock apiClient — never hit real fetch under test.
vi.mock('../../../data/apiClient', () => ({
  getFrequencyHealth: vi.fn(),
  createOrUpdateEvidenceTracking: vi.fn(),
}))

import {
  getFrequencyHealth,
  createOrUpdateEvidenceTracking,
} from '../../../data/apiClient'

const sampleItem: FrequencyHealthItem = {
  evidence_id: 'E-BCM-11',
  declared_frequency: 'monthly',
  suggested_frequency: 'daily',
  observed_cadence_days: 1.2,
  confidence: 'high',
  file_count: 24,
  misaligned: true,
  reason: 'Declared monthly but uploaded ~daily over 28-day window',
}

const sampleReport: FrequencyHealthResponse = {
  organization_id: 'org-1',
  computed_at: '2026-05-09T20:00:00Z',
  evaluation_window_days: 28,
  total_evidence_ids_evaluated: 12,
  misaligned_count: 1,
  low_confidence_count: 2,
  items: [sampleItem],
}

beforeEach(() => {
  vi.clearAllMocks()
  cleanup()
})

describe('FrequencyHealthTile', () => {
  it('renders the loading skeleton while fetching', () => {
    vi.mocked(getFrequencyHealth).mockReturnValueOnce(
      new Promise(() => {
        /* never resolves */
      }),
    )
    render(<FrequencyHealthTile orgId="org-1" />)
    expect(
      screen.getByTestId('frequency-health-tile-loading'),
    ).toBeInTheDocument()
  })

  it('renders the error state with a retry button on fetch failure', async () => {
    vi.mocked(getFrequencyHealth).mockRejectedValueOnce(new Error('500: oops'))
    render(<FrequencyHealthTile orgId="org-1" />)
    await waitFor(() =>
      expect(
        screen.getByTestId('frequency-health-tile-error'),
      ).toBeInTheDocument(),
    )
    expect(screen.getByText(/500: oops/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /retry/i })).toBeInTheDocument()
  })

  it('renders the empty state when misaligned_count is 0', async () => {
    vi.mocked(getFrequencyHealth).mockResolvedValueOnce({
      ...sampleReport,
      misaligned_count: 0,
      items: [],
    })
    render(<FrequencyHealthTile orgId="org-1" />)
    await waitFor(() =>
      expect(
        screen.getByTestId('frequency-health-tile-empty'),
      ).toBeInTheDocument(),
    )
    expect(screen.getByText(/no misaligned cadences detected/i)).toBeInTheDocument()
  })

  it('renders the count and per-evidence rows when expanded', async () => {
    vi.mocked(getFrequencyHealth).mockResolvedValueOnce(sampleReport)
    render(<FrequencyHealthTile orgId="org-1" />)
    await waitFor(() =>
      expect(screen.getByTestId('frequency-health-tile')).toBeInTheDocument(),
    )
    expect(screen.getByTestId('frequency-health-count')).toHaveTextContent(
      /1 misaligned/i,
    )

    // List collapsed by default — expand it.
    fireEvent.click(screen.getByTestId('frequency-health-toggle'))
    expect(screen.getByTestId('frequency-health-list')).toBeInTheDocument()
    expect(
      screen.getByTestId('frequency-health-row-E-BCM-11'),
    ).toBeInTheDocument()
    expect(
      screen.getByTestId('frequency-health-apply-E-BCM-11'),
    ).toBeInTheDocument()
  })

  it('calls createOrUpdateEvidenceTracking when Apply fix is clicked', async () => {
    vi.mocked(getFrequencyHealth).mockResolvedValueOnce(sampleReport)
    vi.mocked(createOrUpdateEvidenceTracking).mockResolvedValueOnce({
      id: 'tr-1',
      organization_id: 'org-1',
      evidence_id: 'E-BCM-11',
      frequency: 'daily',
      created_at: '2026-05-09T20:00:00Z',
      updated_at: '2026-05-09T20:00:00Z',
    })
    // Second fetch after apply — return zero misaligned to confirm refresh.
    vi.mocked(getFrequencyHealth).mockResolvedValueOnce({
      ...sampleReport,
      misaligned_count: 0,
      items: [],
    })

    render(<FrequencyHealthTile orgId="org-1" />)
    await waitFor(() =>
      expect(screen.getByTestId('frequency-health-tile')).toBeInTheDocument(),
    )
    fireEvent.click(screen.getByTestId('frequency-health-toggle'))
    fireEvent.click(screen.getByTestId('frequency-health-apply-E-BCM-11'))

    await waitFor(() => {
      expect(createOrUpdateEvidenceTracking).toHaveBeenCalledWith(
        { evidence_id: 'E-BCM-11', frequency: 'daily' },
        'org-1',
      )
    })

    // After refresh the empty state appears.
    await waitFor(() =>
      expect(
        screen.getByTestId('frequency-health-tile-empty'),
      ).toBeInTheDocument(),
    )
  })

  it('renders the apply error when createOrUpdateEvidenceTracking rejects', async () => {
    vi.mocked(getFrequencyHealth).mockResolvedValueOnce(sampleReport)
    vi.mocked(createOrUpdateEvidenceTracking).mockRejectedValueOnce(
      new Error('403: forbidden'),
    )

    render(<FrequencyHealthTile orgId="org-1" />)
    await waitFor(() =>
      expect(screen.getByTestId('frequency-health-tile')).toBeInTheDocument(),
    )
    fireEvent.click(screen.getByTestId('frequency-health-toggle'))
    fireEvent.click(screen.getByTestId('frequency-health-apply-E-BCM-11'))

    await waitFor(() =>
      expect(
        screen.getByTestId('frequency-health-apply-error'),
      ).toHaveTextContent(/403: forbidden/),
    )
  })
})
