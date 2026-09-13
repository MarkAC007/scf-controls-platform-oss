/**
 * Evidence storage migration panel — the Phase 6 copy, in the seam Phase 5
 * left for it (`EvidenceStorageSettings`'s `migrationPanel` prop).
 *
 * The shape follows the order an administrator actually works in: configure a
 * new store, test it, activate it, and only then move what is already written.
 * So the target of a copy is always the store in force now, and the source is
 * one of this organisation's earlier stores — which activation left `retired`,
 * because the row has to survive for the files still pointing at it to stay
 * resolvable.
 *
 * Three things this panel is careful about, each of them a claim the platform
 * would otherwise be making falsely:
 *
 *   - It never says the source has been retired on its own arithmetic. The
 *     server retires the source only when no evidence file references it any
 *     more, and `source_retired` on the run is the only thing this component
 *     will repeat. A copy that left rows behind says so instead.
 *   - Progress is read back from the run endpoint, never inferred from the
 *     request that started it. A page reloaded mid-copy picks the run up again
 *     from the run list, because the run lives on the server, not here.
 *   - A failure row shows the key and the class of failure the task recorded.
 *     Those reasons are written without a response body, a URL or a
 *     credential in them, and this component adds nothing to them.
 */

import { useCallback, useEffect, useRef, useState } from 'react'

import {
  getEvidenceStorageCopyRun,
  isCopyRunFinished,
  listEvidenceStorageConfigs,
  listEvidenceStorageCopyRuns,
  listEvidenceStorageCopySources,
  startEvidenceStorageCopy,
} from '../data/evidenceStorageApi'
import type {
  EvidenceStorageApiError,
  EvidenceStorageConfig,
  EvidenceStorageCopyRun,
  EvidenceStorageCopySource,
} from '../data/evidenceStorageApi'

/** How often a live run is re-read. Slow enough to be polite to a worker that
 *  is doing real I/O, fast enough that a small copy does not look stuck. */
export const COPY_POLL_MS = 2000

export interface EvidenceStorageMigrationPanelProps {
  organizationId: string
}

function messageOf(err: unknown, fallback: string): string {
  const text = (err as Error | null)?.message
  return text && text.trim() ? text : fallback
}

/** A configuration named the way an admin recognises it: what it is, where. */
export function labelFor(row: EvidenceStorageConfig): string {
  const where = row.endpoint_url ? ` at ${row.endpoint_url}` : ''
  return `${row.provider_label || row.provider} — ${row.bucket}${where}`
}

/** A copy source named the way an admin recognises it.
 *
 *  A platform-scope entry is labelled as the installation's own store and
 *  never as something this organisation manages: it is read-only here, the
 *  copy will not retire it, and other tenants keep writing to it. The wording
 *  matches the settings card's chip for the same row (D42/D48) — this panel
 *  never renders `is_bundled` as a control, and never links the id. */
export function labelForSource(row: EvidenceStorageCopySource): string {
  const where = row.endpoint_url ? ` at ${row.endpoint_url}` : ''
  const base = `${row.provider_label || row.provider} — ${row.bucket}${where}`
  return row.scope === 'platform'
    ? `${base} (managed by the platform, shared)`
    : `${base} (${row.status})`
}

/** How far through the run is, as a whole percentage.
 *
 *  Settled rows over total, not copied over total: a row that failed and a row
 *  that was already at the target are both done being worked on, and a bar
 *  that never fills because two rows failed is a bar that lies about whether
 *  the job is still running. */
export function percentOf(run: EvidenceStorageCopyRun): number {
  if (!run.total) return isCopyRunFinished(run) ? 100 : 0
  const settled = run.copied + run.failed + run.skipped
  return Math.max(0, Math.min(100, Math.round((settled / run.total) * 100)))
}

const STATUS_LABEL: Record<string, string> = {
  queued: 'Queued',
  running: 'Copying',
  completed: 'Finished',
  completed_with_errors: 'Finished with errors',
  failed: 'Stopped',
}

/**
 * What to tell the operator about a finished run.
 *
 * The retirement sentence is conditional on the server's own `source_retired`,
 * never on this component's view of the counts, because the server retires the
 * source only when nothing references it — including files written by another
 * session while the copy was running.
 */
export function completionMessage(run: EvidenceStorageCopyRun): string {
  if (run.status === 'failed') {
    return run.message || 'The copy stopped before it finished. Nothing was removed from the source store.'
  }
  const moved = `${run.copied} of ${run.total} file${run.total === 1 ? '' : 's'} copied`
  const skipped = run.skipped ? `, ${run.skipped} already there` : ''
  const failed = run.failed ? `, ${run.failed} left on the source store` : ''
  // Never claim a retirement this component worked out for itself, and never
  // claim one at all for a store the server says it left in service. A copy
  // out of the shared platform store retires nothing: other tenants are still
  // writing to it, and telling an operator otherwise is the one sentence here
  // that could get a bucket emptied.
  const retirement = run.source_retired
    ? ' The source configuration has been retired: no evidence file references it any more.'
    : run.source_retired_reason
      ? ` The source store was not retired: ${run.source_retired_reason}.`
      : run.remaining
        ? ` The source configuration is still in use by ${run.remaining} file${
            run.remaining === 1 ? '' : 's'
          }, so it has not been retired.`
        : ' The source configuration was left in place.'
  return `${moved}${skipped}${failed}.${retirement}`
}

export default function EvidenceStorageMigrationPanel({
  organizationId,
}: EvidenceStorageMigrationPanelProps) {
  const [configs, setConfigs] = useState<EvidenceStorageConfig[]>([])
  const [sources, setSources] = useState<EvidenceStorageCopySource[]>([])
  const [loading, setLoading] = useState(true)
  const [unavailable, setUnavailable] = useState(false)
  const [readOnly, setReadOnly] = useState(false)

  const [sourceId, setSourceId] = useState('')
  const [confirming, setConfirming] = useState(false)
  const [starting, setStarting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const [run, setRun] = useState<EvidenceStorageCopyRun | null>(null)

  // Set false by the cleanup of the mount effect. Polling is a chain of
  // timeouts rather than an interval, so that a slow response cannot stack
  // requests up behind itself; this flag is what stops the chain.
  const alive = useRef(true)

  useEffect(() => {
    alive.current = true
    let cancelled = false

    const load = async () => {
      const [listResult, runsResult, sourcesResult] = await Promise.allSettled([
        listEvidenceStorageConfigs(organizationId),
        listEvidenceStorageCopyRuns(organizationId),
        listEvidenceStorageCopySources(organizationId),
      ])
      if (cancelled) return

      // The source list is its own question — "where is this organisation's
      // evidence" rather than "what does it own" — because after the first
      // activation the files written before it are stamped with the platform
      // row, which the configuration list does not and should not carry.
      setSources(
        sourcesResult.status === 'fulfilled' ? sourcesResult.value : []
      )

      if (listResult.status === 'fulfilled') {
        setConfigs(listResult.value.items)
        setUnavailable(false)
      } else {
        const status = (listResult.reason as EvidenceStorageApiError)?.status
        setConfigs([])
        setReadOnly(status === 403)
        setUnavailable(status !== 403)
      }

      // A run belongs to the server, not to this page view. Adopting the most
      // recent one means a reload during a long copy shows the copy, rather
      // than an idle panel offering to start a second one.
      if (runsResult.status === 'fulfilled' && runsResult.value.length > 0) {
        setRun(runsResult.value[0])
      }
      setLoading(false)
    }

    void load()
    return () => {
      cancelled = true
      alive.current = false
    }
  }, [organizationId])

  const runId = run?.run_id ?? null
  const finished = isCopyRunFinished(run)

  // Poll while a run is live. The dependency is the id and the finished flag,
  // not the run object, so a poll that returns an unchanged record does not
  // restart the timer twice.
  useEffect(() => {
    if (!runId || finished) return
    let timer: ReturnType<typeof setTimeout> | null = null
    let stopped = false

    const tick = async () => {
      try {
        const next = await getEvidenceStorageCopyRun(organizationId, runId)
        if (stopped || !alive.current) return
        setRun(next)
        if (!isCopyRunFinished(next)) {
          timer = setTimeout(() => void tick(), COPY_POLL_MS)
        }
      } catch (err) {
        if (stopped || !alive.current) return
        // A transient failure to read progress is not a failed copy: the copy
        // is running in a worker regardless. Say what happened and keep
        // polling rather than declaring an outcome we do not know.
        setError(messageOf(err, 'Failed to read the copy progress'))
        timer = setTimeout(() => void tick(), COPY_POLL_MS)
      }
    }

    timer = setTimeout(() => void tick(), COPY_POLL_MS)
    return () => {
      stopped = true
      if (timer) clearTimeout(timer)
    }
  }, [organizationId, runId, finished])

  const target = configs.find((row) => row.status === 'active') ?? null

  const handleStart = useCallback(async () => {
    if (!target || !sourceId) return
    setStarting(true)
    setError(null)
    try {
      const started = await startEvidenceStorageCopy(organizationId, sourceId, target.id)
      setRun(started)
      setConfirming(false)
    } catch (err) {
      setError(messageOf(err, 'Failed to start the copy'))
    } finally {
      setStarting(false)
    }
  }, [organizationId, sourceId, target])

  if (loading) {
    return (
      <section className="evidence-storage-migration" data-testid="evidence-storage-migration">
        <h3>Move existing evidence</h3>
        <p className="integration-health-lead">Loading…</p>
      </section>
    )
  }

  if (readOnly) return null

  const live = run && !isCopyRunFinished(run)

  return (
    <section className="evidence-storage-migration" data-testid="evidence-storage-migration">
      <h3>Move existing evidence</h3>
      <p className="settings-card-sub">
        Activating a store changes where new evidence is written. Files already written stay
        where they are until they are copied. A copy verifies every object by size and
        checksum, moves one file at a time so it can be re-run safely, and never removes
        anything from the store it is reading.
      </p>

      {unavailable && (
        <p className="integration-health-lead" data-testid="evidence-storage-migration-unavailable">
          The configuration list could not be read, so a copy cannot be set up right now.
        </p>
      )}

      {!unavailable && !target && (
        <p className="integration-health-lead" data-testid="evidence-storage-migration-no-target">
          No store of this organisation&rsquo;s own is active, so there is nowhere to copy to.
          Configure a store, test it, and activate it first.
        </p>
      )}

      {!unavailable && target && sources.length === 0 && (
        <p className="integration-health-lead" data-testid="evidence-storage-migration-no-source">
          No other store holds evidence for this organisation, so there is nothing to copy
          from.
        </p>
      )}

      {!unavailable && target && sources.length > 0 && !live && (
        <div className="evidence-storage-migration-start">
          <label htmlFor="evidence-storage-copy-source">Copy evidence to this store from</label>
          <select
            id="evidence-storage-copy-source"
            data-testid="evidence-storage-copy-source"
            value={sourceId}
            disabled={starting}
            onChange={(event) => {
              setSourceId(event.target.value)
              setConfirming(false)
            }}
          >
            <option value="">Choose a configuration…</option>
            {sources.map((row) => (
              <option key={row.config_id} value={row.config_id}>
                {labelForSource(row)}
              </option>
            ))}
          </select>
          <p className="integration-editor-hint">
            Into <strong>{labelFor(target)}</strong>, the store in force now.
          </p>

          {!confirming ? (
            <button
              type="button"
              className="btn btn-primary"
              data-testid="evidence-storage-copy-start"
              disabled={!sourceId}
              onClick={() => {
                setError(null)
                setConfirming(true)
              }}
            >
              Copy evidence to this store
            </button>
          ) : (
            <div
              className="integration-confirm"
              role="alertdialog"
              aria-label="Confirm evidence copy"
            >
              <span>
                Copy every evidence file held under the chosen configuration into{' '}
                {labelFor(target)}? Each file is verified after it is written. Nothing is
                deleted from the source store, and the source configuration is retired only
                once no file references it.
              </span>
              <button
                type="button"
                className="btn btn-primary"
                data-testid="evidence-storage-copy-confirm"
                disabled={starting}
                onClick={() => void handleStart()}
              >
                {starting ? 'Starting…' : 'Yes, copy the evidence'}
              </button>
              <button
                type="button"
                className="btn"
                data-testid="evidence-storage-copy-cancel"
                disabled={starting}
                onClick={() => setConfirming(false)}
              >
                Cancel
              </button>
            </div>
          )}
        </div>
      )}

      {run && (
        <div className="evidence-storage-copy-run" data-testid="evidence-storage-copy-run">
          <div className="integration-row-status">
            <span className="chip" data-testid="evidence-storage-copy-status">
              {STATUS_LABEL[run.status] ?? run.status}
            </span>
            <span data-testid="evidence-storage-copy-counts">
              {run.copied + run.failed + run.skipped} of {run.total} files
            </span>
          </div>

          <div
            className="progress-bar"
            role="progressbar"
            aria-label="Evidence copy progress"
            aria-valuemin={0}
            aria-valuemax={100}
            aria-valuenow={percentOf(run)}
            data-testid="evidence-storage-copy-progress"
          >
            <div
              className="progress-fill progress-fill-info"
              style={{ width: `${percentOf(run)}%` }}
            />
          </div>

          {isCopyRunFinished(run) ? (
            <p
              className="integration-health-lead"
              data-testid="evidence-storage-copy-complete"
              role="status"
            >
              {completionMessage(run)}
            </p>
          ) : (
            <p className="integration-health-lead" data-testid="evidence-storage-copy-live">
              {run.remaining} file{run.remaining === 1 ? '' : 's'} still to go. You can leave
              this page; the copy runs on the server and is safe to re-run.
            </p>
          )}

          {run.failures.length > 0 && (
            <>
              <p className="integration-health-lead">
                These files were not copied and are still on the source store:
              </p>
              <ul
                className="integration-health-list"
                data-testid="evidence-storage-copy-failures"
              >
                {run.failures.map((failure) => (
                  <li key={failure.s3_key}>
                    <span className="integration-health-name">{failure.s3_key}</span>
                    <span>{failure.reason}</span>
                  </li>
                ))}
              </ul>
            </>
          )}
        </div>
      )}

      {error && (
        <p
          className="integration-row-error"
          role="alert"
          data-testid="evidence-storage-copy-error"
        >
          {error}
        </p>
      )}
    </section>
  )
}
