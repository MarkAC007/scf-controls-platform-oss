/**
 * useAssessmentPolling — failure is reported, never swallowed (#881).
 *
 * The hook used to eat every failure: a bare `catch {}` on trigger, a
 * "silently stop polling" catch, and a `.catch(() => {})` on the initial
 * fetch. The user-visible result was a panel that sat on "No AI assessment
 * yet" while the request behind it had 500'd — the worst possible reading,
 * because it is indistinguishable from a file nobody has assessed.
 *
 * These tests pin the distinction the panel has to be able to draw:
 *   - a stored verdict of `status: 'error'` — the model ran and failed;
 *   - a `requestError` — we could not find out what the verdict is.
 * They are different claims and the UI must never collapse them into one.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { renderHook, waitFor, act } from '@testing-library/react'

vi.mock('../../data/apiClient', () => ({
  getAssessment: vi.fn(),
  triggerAssessment: vi.fn(),
}))

import { getAssessment, triggerAssessment, type EvidenceAssessmentResponse } from '../../data/apiClient'
import { useAssessmentPolling } from '../useAssessmentPolling'

function makeAssessment(
  overrides: Partial<EvidenceAssessmentResponse> = {},
): EvidenceAssessmentResponse {
  return {
    id: 'assess-1',
    evidence_file_id: 'file-1',
    organization_id: 'org-1',
    evidence_id: 'ERL-001',
    status: 'sufficient',
    relevance_score: 88,
    findings: [],
    summary: 'Covers the control.',
    model_id: 'claude-sonnet-5',
    prompt_hash: 'abc123',
    prompt_version: 'v3',
    control_context_hash: 'def456',
    framework_version: '2025.1',
    input_token_count: 100,
    output_token_count: 50,
    cost_cents: 2,
    processing_time_ms: 1200,
    assessment_source: 'on_demand',
    requested_by_user_id: 'user-1',
    assessed_at: '2026-09-01T10:00:00Z',
    created_at: '2026-09-01T10:00:00Z',
    truncated: false,
    truncated_at_chars: null,
    cached: false,
    ...overrides,
  }
}

function renderPolling() {
  return renderHook(() => useAssessmentPolling('org-1', 'ERL-001', 'file-1'))
}

describe('useAssessmentPolling error surfacing', () => {
  beforeEach(() => vi.clearAllMocks())
  afterEach(() => vi.useRealTimers())

  it('reports a failed initial load instead of pretending nothing was assessed', async () => {
    vi.mocked(getAssessment).mockRejectedValue(new Error('Internal Server Error'))

    const { result } = renderPolling()

    await waitFor(() => expect(result.current.loading).toBe(false))
    expect(result.current.requestError).toEqual({
      kind: 'load',
      message: 'Internal Server Error',
    })
    // Crucially: no assessment AND an error. "Nothing yet" would be a lie.
    expect(result.current.assessment).toBeNull()
  })

  it('treats a genuine absence (null) as an absence, not an error', async () => {
    vi.mocked(getAssessment).mockResolvedValue(null)

    const { result } = renderPolling()

    await waitFor(() => expect(result.current.loading).toBe(false))
    expect(result.current.requestError).toBeNull()
    expect(result.current.assessment).toBeNull()
  })

  it('keeps a stored error verdict separate from a request failure', async () => {
    vi.mocked(getAssessment).mockResolvedValue(makeAssessment({ status: 'error' }))

    const { result } = renderPolling()

    await waitFor(() => expect(result.current.loading).toBe(false))
    // The model ran and failed — that IS the answer, so there is no requestError.
    expect(result.current.assessment?.status).toBe('error')
    expect(result.current.requestError).toBeNull()
  })

  it('reports a trigger failure rather than leaving the button looking idle', async () => {
    vi.mocked(getAssessment).mockResolvedValue(null)
    vi.mocked(triggerAssessment).mockRejectedValue(new Error('Assessment service unavailable'))

    const { result } = renderPolling()
    await waitFor(() => expect(result.current.loading).toBe(false))

    await act(async () => {
      await result.current.trigger()
    })

    expect(result.current.requestError).toEqual({
      kind: 'trigger',
      message: 'Assessment service unavailable',
    })
    expect(result.current.triggering).toBe(false)
  })

  it('sends force only when the caller asks for a deliberate re-run', async () => {
    vi.mocked(getAssessment).mockResolvedValue(makeAssessment())
    vi.mocked(triggerAssessment).mockResolvedValue(makeAssessment({ status: 'pending' }))

    const { result } = renderPolling()
    await waitFor(() => expect(result.current.loading).toBe(false))

    await act(async () => {
      await result.current.trigger({ force: true })
    })

    expect(triggerAssessment).toHaveBeenCalledWith(
      'org-1', 'ERL-001', 'file-1', 'on_demand', true,
    )
  })

  it('clears a previous failure once a request succeeds', async () => {
    vi.mocked(getAssessment).mockRejectedValue(new Error('boom'))

    const { result } = renderPolling()
    await waitFor(() => expect(result.current.requestError).not.toBeNull())

    vi.mocked(triggerAssessment).mockResolvedValue(makeAssessment({ status: 'pending' }))
    await act(async () => {
      await result.current.trigger()
    })

    expect(result.current.requestError).toBeNull()
  })

  it('retry re-runs the fetch that failed', async () => {
    vi.mocked(getAssessment).mockRejectedValueOnce(new Error('transient'))

    const { result } = renderPolling()
    await waitFor(() => expect(result.current.requestError).not.toBeNull())

    vi.mocked(getAssessment).mockResolvedValue(makeAssessment())
    await act(async () => {
      await result.current.retry()
    })

    expect(result.current.requestError).toBeNull()
    expect(result.current.assessment?.status).toBe('sufficient')
  })

  it('rides out a transient poll failure, then admits it has lost contact', async () => {
    vi.useFakeTimers()
    // Mount in-progress so the hook starts polling.
    vi.mocked(getAssessment).mockResolvedValueOnce(makeAssessment({ status: 'processing' }))
    // Then fail every poll.
    vi.mocked(getAssessment).mockRejectedValue(new Error('network down'))

    const { result } = renderPolling()
    await vi.waitFor(() => expect(result.current.loading).toBe(false))

    // Step the backoff one poll at a time (3000ms, then x1.5 each failure) —
    // advancing far enough to fire all three at once would not test the
    // tolerance, only the give-up.
    await act(async () => { await vi.advanceTimersByTimeAsync(3_100) })
    expect(result.current.requestError).toBeNull()

    await act(async () => { await vi.advanceTimersByTimeAsync(4_600) })
    expect(result.current.requestError).toBeNull()

    // The third consecutive failure is no longer a blip, and is stated.
    await act(async () => { await vi.advanceTimersByTimeAsync(6_800) })
    expect(result.current.requestError?.kind).toBe('poll')

    // And it stops asking. A panel that has admitted it lost contact must not
    // also be quietly still polling.
    const callsAtGiveUp = vi.mocked(getAssessment).mock.calls.length
    await act(async () => { await vi.advanceTimersByTimeAsync(120_000) })
    expect(vi.mocked(getAssessment).mock.calls.length).toBe(callsAtGiveUp)
  })

  it('stops polling on a verdict it does not recognise, rather than forever', async () => {
    // WS2 is reworking the verdict vocabulary. A hook that polls until it sees
    // a known terminal status would poll a settled row indefinitely the moment
    // a new advisory term ships, so the in-progress set is what drives it.
    vi.useFakeTimers()
    vi.mocked(getAssessment).mockResolvedValueOnce(makeAssessment({ status: 'processing' }))
    vi.mocked(getAssessment).mockResolvedValue(makeAssessment({ status: 'gap_identified' }))

    const { result } = renderPolling()
    await vi.waitFor(() => expect(result.current.loading).toBe(false))

    await act(async () => { await vi.advanceTimersByTimeAsync(3_100) })
    expect(result.current.assessment?.status).toBe('gap_identified')

    const callsAfterSettling = vi.mocked(getAssessment).mock.calls.length
    await act(async () => { await vi.advanceTimersByTimeAsync(300_000) })
    expect(vi.mocked(getAssessment).mock.calls.length).toBe(callsAfterSettling)
  })
})
