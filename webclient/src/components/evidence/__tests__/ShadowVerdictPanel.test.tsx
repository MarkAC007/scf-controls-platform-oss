/**
 * ShadowVerdictPanel — Jev's verdict beside Claude's, in the file preview.
 *
 * What these tests defend:
 *
 *   - no shadow verdict means no panel at all, not an empty box;
 *   - the agreement summary counts what the backend compared, and a
 *     disagreement shows Claude's designation next to Jev's so a reviewer can
 *     see both without leaving the row;
 *   - a confidence below the cutoff is marked, because that is the figure a
 *     cutover decision leans on;
 *   - a failed shadow run is shown as a failure, not hidden;
 *   - a new assessment version re-reads the shadow;
 *   - a shadow row from an earlier version is never shown beside the new
 *     Claude verdict: it is held back and re-read until this version's lands.
 */
import { act, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { SHADOW_POLL_LIMIT, SHADOW_POLL_MS, ShadowVerdictPanel } from '../ShadowVerdictPanel'
import { getShadowVerdict } from '../../../data/assessmentEngineApi'
import type { ShadowVerdict } from '../../../data/assessmentEngineApi'

vi.mock('../../../data/assessmentEngineApi', () => ({
  getShadowVerdict: vi.fn(),
}))

const mockGet = vi.mocked(getShadowVerdict)

function verdict(overrides: Partial<ShadowVerdict> = {}): ShadowVerdict {
  return {
    id: 'sv-1',
    evidence_file_id: 'file-1',
    assessment_id: 'a-1',
    version_id: null,
    engine: 'jev',
    model_id: 'jev-1.13.0',
    status: 'partial',
    relevance_score: 62.5,
    ao_findings: [
      {
        ao_id: 'AO0001',
        suggested_designation: 'appears_satisfied',
        confidence: 0.93,
        probabilities: { appears_satisfied: 0.93 },
      },
      {
        ao_id: 'AO0002',
        suggested_designation: 'appears_satisfied',
        confidence: 0.41,
        probabilities: { appears_satisfied: 0.41 },
      },
    ],
    gap_count: 0,
    cannot_assess_count: 0,
    low_confidence_count: 1,
    confidence_cutoff: 0.85,
    comparison: {
      compared: 4,
      agreed: 3,
      agreement_rate: 0.75,
      confident_total: 3,
      confident_agreed: 3,
      confident_agreement_rate: 1.0,
      llm_status: 'partial',
      jev_status: 'partial',
      status_agrees: true,
      disagreements: [{ ao_id: 'AO0002', llm: 'gap_identified', jev: 'appears_satisfied', confidence: 0.41 }],
    },
    state_truncated: false,
    input_token_count: 1200,
    output_token_count: 0,
    cost_cents: 0.005,
    processing_time_ms: 640,
    error: null,
    created_at: '2026-09-29T06:00:00',
    ...overrides,
  }
}

const PROPS = { orgId: 'org-1', evidenceId: 'ev-1', fileId: 'file-1' }

beforeEach(() => {
  vi.clearAllMocks()
})

describe('ShadowVerdictPanel', () => {
  it('renders nothing when there is no shadow verdict', async () => {
    mockGet.mockResolvedValue(null)
    const { container } = render(<ShadowVerdictPanel {...PROPS} />)
    await waitFor(() => expect(mockGet).toHaveBeenCalledWith('org-1', 'ev-1', 'file-1'))
    expect(container).toBeEmptyDOMElement()
  })

  it('renders nothing when the read fails', async () => {
    const spy = vi.spyOn(console, 'error').mockImplementation(() => {})
    mockGet.mockRejectedValue(new Error('boom'))
    const { container } = render(<ShadowVerdictPanel {...PROPS} />)
    await waitFor(() => expect(mockGet).toHaveBeenCalled())
    expect(container).toBeEmptyDOMElement()
    spy.mockRestore()
  })

  it('summarises agreement and lists each objective', async () => {
    mockGet.mockResolvedValue(verdict())
    render(<ShadowVerdictPanel {...PROPS} assessmentVersion={2} />)

    const panel = await screen.findByTestId('shadow-verdict-panel')
    expect(panel).toHaveTextContent('Jev shadow verdict')
    expect(panel).toHaveTextContent('Agrees with Claude on 3 of 4 objectives (75%; confident subset 3/3)')

    const rows = screen.getAllByTestId('shadow-verdict-row')
    expect(rows).toHaveLength(2)
    expect(rows[0]).toHaveTextContent('AO0001')
    expect(rows[0]).toHaveTextContent('Appears satisfied')
    expect(rows[0]).toHaveTextContent('93%')
    expect(rows[0]).not.toHaveTextContent('Claude')

    // The disagreement carries Claude's side, and the low confidence is marked.
    expect(rows[1]).toHaveTextContent('41%')
    expect(rows[1]).toHaveTextContent('Claude: Gap identified')
    expect(rows[1].querySelector('.shadow-verdict-confidence.low')).not.toBeNull()
    expect(rows[0].querySelector('.shadow-verdict-confidence.low')).toBeNull()
  })

  it('says so when the row carries no comparison', async () => {
    mockGet.mockResolvedValue(verdict({ comparison: null }))
    render(<ShadowVerdictPanel {...PROPS} />)
    expect(await screen.findByText('No comparison was recorded for this shadow run')).toBeInTheDocument()
  })

  it('treats an unreported confidence as below the cutoff', async () => {
    mockGet.mockResolvedValue(
      verdict({
        ao_findings: [
          { ao_id: 'AO0001', suggested_designation: 'appears_satisfied', confidence: null, probabilities: {} },
        ],
      }),
    )
    render(<ShadowVerdictPanel {...PROPS} />)
    const [row] = await screen.findAllByTestId('shadow-verdict-row')
    expect(row).toHaveTextContent('—')
    expect(row.querySelector('.shadow-verdict-confidence.low')).not.toBeNull()
    expect(row.querySelector('.shadow-verdict-confidence')?.getAttribute('title')).toBe(
      'No confidence figure was reported',
    )
  })

  it('shows a failed shadow run as a failure', async () => {
    mockGet.mockResolvedValue(
      verdict({ comparison: null, ao_findings: [], error: 'TypeSafe API key is not configured' }),
    )
    render(<ShadowVerdictPanel {...PROPS} />)
    expect(
      await screen.findByText('Shadow assessment failed: TypeSafe API key is not configured'),
    ).toBeInTheDocument()
    expect(screen.queryByText('No comparison was recorded for this shadow run')).not.toBeInTheDocument()
  })

  it('shows a shadow whose version is the one on screen', async () => {
    mockGet.mockResolvedValue(verdict({ version_id: 'v-4' }))
    render(<ShadowVerdictPanel {...PROPS} assessmentVersion={4} assessmentVersionId="v-4" />)
    expect(await screen.findByTestId('shadow-verdict-panel')).toBeInTheDocument()
    expect(mockGet).toHaveBeenCalledTimes(1)
  })

  it('holds back a shadow from an earlier version and re-reads until this one lands', async () => {
    vi.useFakeTimers()
    try {
      mockGet
        .mockResolvedValueOnce(verdict({ version_id: 'v-3' }))
        .mockResolvedValueOnce(verdict({ version_id: 'v-3' }))
        .mockResolvedValueOnce(verdict({ version_id: 'v-4' }))
      const { container } = render(
        <ShadowVerdictPanel {...PROPS} assessmentVersion={4} assessmentVersionId="v-4" />,
      )
      await act(async () => {})
      expect(mockGet).toHaveBeenCalledTimes(1)
      // v3's shadow is not shown against v4's verdict.
      expect(container).toBeEmptyDOMElement()

      await act(async () => {
        await vi.advanceTimersByTimeAsync(SHADOW_POLL_MS)
      })
      expect(mockGet).toHaveBeenCalledTimes(2)
      expect(container).toBeEmptyDOMElement()

      await act(async () => {
        await vi.advanceTimersByTimeAsync(SHADOW_POLL_MS)
      })
      expect(mockGet).toHaveBeenCalledTimes(3)
      expect(screen.getByTestId('shadow-verdict-panel')).toBeInTheDocument()

      // Once the right row is on screen the timer is not re-armed.
      await act(async () => {
        await vi.advanceTimersByTimeAsync(SHADOW_POLL_MS * 2)
      })
      expect(mockGet).toHaveBeenCalledTimes(3)
    } finally {
      vi.useRealTimers()
    }
  })

  it('stops re-reading after the wait runs out', async () => {
    vi.useFakeTimers()
    try {
      mockGet.mockResolvedValue(verdict({ version_id: 'v-3' }))
      const { container } = render(
        <ShadowVerdictPanel {...PROPS} assessmentVersion={4} assessmentVersionId="v-4" />,
      )
      await act(async () => {})
      await act(async () => {
        await vi.advanceTimersByTimeAsync(SHADOW_POLL_MS * (SHADOW_POLL_LIMIT + 5))
      })
      expect(mockGet).toHaveBeenCalledTimes(SHADOW_POLL_LIMIT + 1)
      expect(container).toBeEmptyDOMElement()
    } finally {
      vi.useRealTimers()
    }
  })

  it('does not poll when there is no shadow at all', async () => {
    vi.useFakeTimers()
    try {
      mockGet.mockResolvedValue(null)
      render(<ShadowVerdictPanel {...PROPS} assessmentVersion={4} assessmentVersionId="v-4" />)
      await act(async () => {})
      await act(async () => {
        await vi.advanceTimersByTimeAsync(SHADOW_POLL_MS * 3)
      })
      expect(mockGet).toHaveBeenCalledTimes(1)
    } finally {
      vi.useRealTimers()
    }
  })

  it('re-reads the shadow when the assessment version changes', async () => {
    mockGet.mockResolvedValue(verdict())
    const { rerender } = render(<ShadowVerdictPanel {...PROPS} assessmentVersion={1} />)
    await waitFor(() => expect(mockGet).toHaveBeenCalledTimes(1))
    rerender(<ShadowVerdictPanel {...PROPS} assessmentVersion={2} />)
    await waitFor(() => expect(mockGet).toHaveBeenCalledTimes(2))
  })
})
