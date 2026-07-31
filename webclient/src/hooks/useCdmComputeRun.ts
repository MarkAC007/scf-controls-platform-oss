/**
 * Shared state for the CDM mapping run.
 *
 * The run used to be tracked inside the Review-queue tab, which meant the
 * primary action lived two clicks away from the page that says "Ready to
 * map", and switching tabs unmounted the component and discarded all
 * knowledge of a running run. This hook lifts the whole lifecycle —
 * trigger, poll, persistence, bounded-unknown escape — so CDMWorkspace can
 * render one action bar that is visible on both tabs.
 *
 * Persistence: the task id and start time are stored per-org in
 * sessionStorage, so a reload or tab switch resumes tracking. The poll is
 * bounded: Celery results expire after an hour and an expired id reads
 * PENDING forever, so after ~5 minutes without a terminal state the run is
 * declared UNKNOWN and the trigger is handed back.
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import { toast } from 'react-hot-toast'
import {
  triggerCdmComputeMappings,
  getCdmComputeMappingsStatus,
} from '../data/apiClient'

const POLL_INTERVAL_MS = 3000
const MAX_POLL_TICKS = 100 // ~5 minutes

export interface CdmComputeRun {
  /** Raw state: PENDING | STARTED | SUCCESS | FAILURE | REVOKED | UNKNOWN | null */
  state: string | null
  /** True while the trigger request itself is in flight. */
  busy: boolean
  /** True while a tracked run has not reached a settled state. */
  running: boolean
  /** When the tracked run was started (persisted; survives reload). */
  startedAt: Date | null
  /** Kick off a run. No-op while one is already running. */
  start: () => Promise<void>
}

// Settled = the run is over, or we have admitted we cannot know (UNKNOWN,
// after the bounded poll expires). Either way the trigger comes back.
export function isComputeSettled(state: string | null): boolean {
  return (
    state === 'SUCCESS' || state === 'FAILURE' || state === 'REVOKED' || state === 'UNKNOWN'
  )
}

interface StoredRun {
  taskId: string
  startedAt: number
}

function storageKey(organizationId: string): string {
  return `cdm:computeTask:${organizationId}`
}

function readStoredRun(organizationId: string): StoredRun | null {
  try {
    const raw = window.sessionStorage.getItem(storageKey(organizationId))
    if (!raw) return null
    // Earlier versions stored the bare task id; tolerate both shapes.
    if (raw.startsWith('{')) {
      const parsed = JSON.parse(raw) as Partial<StoredRun>
      if (typeof parsed.taskId === 'string') {
        return { taskId: parsed.taskId, startedAt: parsed.startedAt ?? Date.now() }
      }
      return null
    }
    return { taskId: raw, startedAt: Date.now() }
  } catch {
    return null
  }
}

/**
 * @param onSettled invoked once when a tracked run reaches SUCCESS or
 * FAILURE (not UNKNOWN/REVOKED) — the caller refreshes whatever the run
 * feeds (proposal counts, the queue itself).
 */
export function useCdmComputeRun(
  organizationId: string,
  onSettled?: (successful: boolean) => void,
): CdmComputeRun {
  const [stored, setStored] = useState<StoredRun | null>(() => readStoredRun(organizationId))
  const [state, setState] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const onSettledRef = useRef(onSettled)
  useEffect(() => {
    onSettledRef.current = onSettled
  }, [onSettled])

  const clearStored = useCallback(() => {
    try {
      window.sessionStorage.removeItem(storageKey(organizationId))
    } catch {
      /* best-effort */
    }
  }, [organizationId])

  const start = useCallback(async () => {
    setBusy(true)
    try {
      const resp = await triggerCdmComputeMappings(organizationId)
      const run: StoredRun = { taskId: resp.task_id, startedAt: Date.now() }
      setStored(run)
      setState('PENDING')
      try {
        window.sessionStorage.setItem(storageKey(organizationId), JSON.stringify(run))
      } catch {
        /* best-effort */
      }
      toast.success(
        resp.idempotent_existing
          ? 'A mapping run is already in progress — tracking it now.'
          : 'Mapping run started.',
      )
    } catch (err) {
      const raw = err instanceof Error ? err.message : 'Failed to start mapping run'
      if (raw.includes('proposed-mappings cap reached')) {
        toast.error(
          `${raw}. Accept or dismiss enough proposals in the queue to drop below the cap, then try again.`,
          { duration: 8000 },
        )
      } else if (raw.includes('compute lock contention')) {
        toast.error(
          'A previous mapping run is still finishing. Wait a minute or two and try again.',
          { duration: 6000 },
        )
      } else {
        toast.error(raw)
      }
    } finally {
      setBusy(false)
    }
  }, [organizationId])

  useEffect(() => {
    if (!stored) return
    if (isComputeSettled(state)) return

    let cancelled = false
    let ticks = 0
    const tick = async () => {
      ticks += 1
      if (ticks > MAX_POLL_TICKS) {
        setState('UNKNOWN')
        clearStored()
        return
      }
      try {
        const resp = await getCdmComputeMappingsStatus(organizationId, stored.taskId)
        if (cancelled) return
        setState(resp.state)
        if (resp.ready) {
          clearStored()
          if (resp.successful) {
            toast.success('Mapping run complete.')
          } else {
            toast.error('Mapping run failed — see worker logs.')
          }
          onSettledRef.current?.(Boolean(resp.successful))
        }
      } catch {
        /* swallow — next tick retries */
      }
    }
    void tick()
    const handle = window.setInterval(tick, POLL_INTERVAL_MS)
    return () => {
      cancelled = true
      window.clearInterval(handle)
    }
  }, [stored, state, organizationId, clearStored])

  const running = stored !== null && !isComputeSettled(state)
  return {
    state,
    busy,
    running,
    startedAt: stored ? new Date(stored.startedAt) : null,
    start,
  }
}
