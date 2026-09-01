import { useState, useEffect, useRef, useCallback } from 'react'
import {
  getAssessment,
  triggerAssessment,
  type EvidenceAssessmentResponse,
} from '../data/apiClient'

const POLL_INITIAL_MS = 3000
const POLL_MAX_MS = 30000
const POLL_BACKOFF = 1.5

/**
 * How many consecutive poll failures to ride out before saying so.
 *
 * One failed poll in a sequence that may run for minutes is a blip, and
 * blanking the panel over it would be its own kind of dishonesty. Three in a
 * row is not a blip, and at that point the panel has to stop implying it is
 * still watching something.
 */
const POLL_MAX_CONSECUTIVE_FAILURES = 3

/**
 * Is this assessment still running?
 *
 * Deliberately a list of the two in-progress states rather than a list of the
 * settled ones. The verdict vocabulary is being reworked (#881 WS2 introduces
 * advisory terms), and a whitelist of terminal statuses would treat any new
 * verdict as "still running" and poll a settled row forever. The in-progress
 * set is the half that is stable, so an unrecognised status stops the polling
 * rather than driving it. The backend guarantees a queued response is always
 * `pending`, so nothing in flight is missed.
 */
function isInProgress(status: string): boolean {
  return status === 'pending' || status === 'processing'
}

/**
 * A request that failed — as opposed to an assessment that returned a verdict
 * of ``error``.
 *
 * The distinction is the whole point of this type. ``assessment.status ===
 * 'error'`` means the model ran and could not reach a judgement; a
 * ``AssessmentRequestError`` means we never found out what the judgement is.
 * Collapsing the two would tell a reviewer the AI rejected their evidence when
 * in fact the network dropped.
 */
export interface AssessmentRequestError {
  /** ``load`` = initial fetch, ``poll`` = status polling, ``trigger`` = starting a run. */
  kind: 'load' | 'poll' | 'trigger'
  message: string
}

function errorMessage(err: unknown, fallback: string): string {
  return err instanceof Error && err.message ? err.message : fallback
}

/**
 * Hook for fetching and polling AI assessment status for a single evidence file.
 *
 * - Fetches assessment on mount
 * - If status is pending/processing, polls with exponential backoff
 * - Provides trigger() to start a new assessment, and retry() to re-run a
 *   fetch that failed
 * - Surfaces every failure through ``requestError``. Nothing here swallows an
 *   error: a caller that cannot tell "no assessment" from "could not ask"
 *   cannot render an honest panel.
 */
export function useAssessmentPolling(
  orgId: string,
  evidenceId: string,
  fileId: string,
) {
  const [assessment, setAssessment] = useState<EvidenceAssessmentResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [triggering, setTriggering] = useState(false)
  const [requestError, setRequestError] = useState<AssessmentRequestError | null>(null)
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const intervalRef = useRef(POLL_INITIAL_MS)
  const pollFailuresRef = useRef(0)
  const mountedRef = useRef(true)

  const stopPolling = useCallback(() => {
    if (timerRef.current) {
      clearTimeout(timerRef.current)
      timerRef.current = null
    }
  }, [])

  const poll = useCallback(async () => {
    if (!mountedRef.current) return
    try {
      const result = await getAssessment(orgId, evidenceId, fileId)
      if (!mountedRef.current) return
      pollFailuresRef.current = 0
      setRequestError(null)
      setAssessment(result)

      if (result && isInProgress(result.status)) {
        intervalRef.current = Math.min(intervalRef.current * POLL_BACKOFF, POLL_MAX_MS)
        timerRef.current = setTimeout(poll, intervalRef.current)
      }
    } catch (err: unknown) {
      if (!mountedRef.current) return
      pollFailuresRef.current += 1
      if (pollFailuresRef.current >= POLL_MAX_CONSECUTIVE_FAILURES) {
        // Give up and say so. The last assessment we did see stays on screen —
        // it was true when we fetched it — but the panel now also carries the
        // fact that we have stopped being able to confirm it.
        setRequestError({
          kind: 'poll',
          message: errorMessage(err, 'Lost contact while checking assessment status'),
        })
        return
      }
      intervalRef.current = Math.min(intervalRef.current * POLL_BACKOFF, POLL_MAX_MS)
      timerRef.current = setTimeout(poll, intervalRef.current)
    }
  }, [orgId, evidenceId, fileId])

  const load = useCallback(async () => {
    setLoading(true)
    setRequestError(null)
    intervalRef.current = POLL_INITIAL_MS
    pollFailuresRef.current = 0
    try {
      const result = await getAssessment(orgId, evidenceId, fileId)
      if (!mountedRef.current) return
      setAssessment(result)
      // Start polling if in-progress
      if (result && isInProgress(result.status)) {
        timerRef.current = setTimeout(poll, intervalRef.current)
      }
    } catch (err: unknown) {
      if (!mountedRef.current) return
      // ``getAssessment`` returns null for a real 404, so reaching here means
      // the request itself failed. Leaving the panel on "No AI assessment yet"
      // would report an outage as a clean bill of health.
      setRequestError({ kind: 'load', message: errorMessage(err, 'Failed to load assessment') })
    } finally {
      if (mountedRef.current) setLoading(false)
    }
  }, [orgId, evidenceId, fileId, poll])

  // Initial fetch
  useEffect(() => {
    mountedRef.current = true
    load()

    return () => {
      mountedRef.current = false
      stopPolling()
    }
  }, [load, stopPolling])

  /**
   * Start an assessment.
   *
   * ``force`` re-runs the model even when a cached verdict exists — what a
   * user means when they press "Re-assess". Without it the backend is entitled
   * to hand back the same cached answer, and the button would appear to do
   * nothing.
   */
  const trigger = useCallback(async (options: { force?: boolean } = {}) => {
    setTriggering(true)
    setRequestError(null)
    stopPolling()
    pollFailuresRef.current = 0
    try {
      const result = await triggerAssessment(
        orgId, evidenceId, fileId, 'on_demand', options.force ?? false,
      )
      if (!mountedRef.current) return
      setAssessment(result)
      // Only poll if the run is actually in flight. A cache hit comes back
      // already terminal, and polling it would just re-fetch a settled row.
      intervalRef.current = POLL_INITIAL_MS
      if (isInProgress(result.status)) {
        timerRef.current = setTimeout(poll, intervalRef.current)
      }
    } catch (err: unknown) {
      if (!mountedRef.current) return
      setRequestError({
        kind: 'trigger',
        message: errorMessage(err, 'Could not start the assessment'),
      })
    } finally {
      if (mountedRef.current) setTriggering(false)
    }
  }, [orgId, evidenceId, fileId, poll, stopPolling])

  /** Re-run the fetch after a failure, without re-running the model. */
  const retry = useCallback(async () => {
    stopPolling()
    await load()
  }, [load, stopPolling])

  return { assessment, loading, triggering, trigger, requestError, retry }
}
