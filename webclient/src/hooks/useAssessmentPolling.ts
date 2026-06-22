import { useState, useEffect, useRef, useCallback } from 'react'
import {
  getAssessment,
  triggerAssessment,
  type EvidenceAssessmentResponse,
} from '../data/apiClient'

const POLL_INITIAL_MS = 3000
const POLL_MAX_MS = 30000
const POLL_BACKOFF = 1.5

function isTerminal(status: string): boolean {
  return ['sufficient', 'partial', 'insufficient', 'error'].includes(status)
}

/**
 * Hook for fetching and polling AI assessment status for a single evidence file.
 *
 * - Fetches assessment on mount
 * - If status is pending/processing, polls with exponential backoff
 * - Provides trigger() to start a new assessment
 */
export function useAssessmentPolling(
  orgId: string,
  evidenceId: string,
  fileId: string,
) {
  const [assessment, setAssessment] = useState<EvidenceAssessmentResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [triggering, setTriggering] = useState(false)
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const intervalRef = useRef(POLL_INITIAL_MS)
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
      setAssessment(result)

      if (result && !isTerminal(result.status)) {
        intervalRef.current = Math.min(intervalRef.current * POLL_BACKOFF, POLL_MAX_MS)
        timerRef.current = setTimeout(poll, intervalRef.current)
      }
    } catch {
      // Silently stop polling on error
    }
  }, [orgId, evidenceId, fileId])

  // Initial fetch
  useEffect(() => {
    mountedRef.current = true
    setLoading(true)
    intervalRef.current = POLL_INITIAL_MS

    getAssessment(orgId, evidenceId, fileId)
      .then(result => {
        if (!mountedRef.current) return
        setAssessment(result)
        // Start polling if in-progress
        if (result && !isTerminal(result.status)) {
          timerRef.current = setTimeout(poll, intervalRef.current)
        }
      })
      .catch(() => {})
      .finally(() => {
        if (mountedRef.current) setLoading(false)
      })

    return () => {
      mountedRef.current = false
      stopPolling()
    }
  }, [orgId, evidenceId, fileId, poll, stopPolling])

  const trigger = useCallback(async () => {
    setTriggering(true)
    stopPolling()
    try {
      const result = await triggerAssessment(orgId, evidenceId, fileId)
      if (!mountedRef.current) return
      setAssessment(result)
      // Start polling for completion
      intervalRef.current = POLL_INITIAL_MS
      timerRef.current = setTimeout(poll, intervalRef.current)
    } catch {
      // Assessment trigger failed — leave current state
    } finally {
      if (mountedRef.current) setTriggering(false)
    }
  }, [orgId, evidenceId, fileId, poll, stopPolling])

  return { assessment, loading, triggering, trigger }
}
