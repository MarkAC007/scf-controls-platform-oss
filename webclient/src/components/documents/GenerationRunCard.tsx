/**
 * Live status of the organisation's document generation run.
 *
 * Lives on the library page, not inside the Generate panel: a run takes
 * minutes, and closing the panel used to take the only progress indicator
 * with it. The card owns the polling and the library refresh at the end of a
 * run for the same reason — they must not depend on the panel being open.
 *
 * Shows the whole batch: a segmented bar with one segment per document, the
 * stage of the document being written, elapsed time and an estimate, and a
 * row per document with its outcome. A finished run stays until dismissed.
 */
import { useEffect, useMemo, useRef, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import {
  getGenerationStatus,
  type GenerationItem,
  type GenerationStatus,
} from '../../data/documentsApi'

/** The pipeline's stages for one document, in order (services/doc_gen/pipeline.py). */
const STAGES: { key: string; label: string }[] = [
  { key: 'checking', label: 'Check' },
  { key: 'loading', label: 'Load data' },
  { key: 'generating', label: 'Write' },
  { key: 'merging', label: 'Merge edits' },
  { key: 'saving', label: 'Save' },
]

const STATE_LABEL: Record<GenerationItem['state'], string> = {
  pending: 'Waiting',
  running: 'Writing',
  created: 'Created',
  updated: 'Updated',
  skipped: 'Unchanged',
  failed: 'Refused',
}

type Tone = 'running' | 'success' | 'warning' | 'danger'

function headline(status: GenerationStatus): { title: string; tone: Tone } {
  switch (status.status) {
    case 'queued':
      return { title: 'Queued — waiting for a worker', tone: 'running' }
    case 'running':
      return { title: 'Generating documents', tone: 'running' }
    case 'completed':
      return { title: 'Generation complete', tone: 'success' }
    case 'completed_with_errors':
      return { title: 'Finished with refusals', tone: 'warning' }
    case 'partial':
      return { title: 'Stopped at the time limit', tone: 'warning' }
    default:
      return { title: 'Generation failed', tone: 'danger' }
  }
}

/**
 * A worker started before the status carried `items` reports only
 * "policy (M-ISMS): Writing chapter 3 of 9: …". Split that into the document
 * and the stage message so the card can still name what is being written.
 */
function parseLegacyMessage(message?: string): { name?: string; scope?: string; detail?: string } {
  const m = message?.match(/^([a-z0-9_]+)(?: \(([^)]+)\))?: (.*)$/i)
  if (!m) return { detail: message }
  const name = m[1].replace(/_/g, ' ')
  return { name: name.charAt(0).toUpperCase() + name.slice(1), scope: m[2], detail: m[3] }
}

function formatDuration(ms: number): string {
  const s = Math.max(0, Math.round(ms / 1000))
  if (s < 60) return `${s}s`
  const m = Math.floor(s / 60)
  return `${m}m ${String(s % 60).padStart(2, '0')}s`
}

interface Props {
  organizationId: string
  /** Opens a finished document from its row. */
  onOpenDocument?: (documentId: string) => void
}

export default function GenerationRunCard({ organizationId, onOpenDocument }: Props) {
  const queryClient = useQueryClient()
  const { data: status } = useQuery({
    queryKey: ['docgen-status', organizationId],
    queryFn: () => getGenerationStatus(organizationId),
    refetchInterval: (query) => {
      const s = query.state.data?.status
      return s === 'running' || s === 'queued' ? 1500 : false
    },
  })

  const running = status?.status === 'running' || status?.status === 'queued'

  // Refresh the library when a run finishes. Polling only tracks the run's own
  // status key; without this the list still reads "No documents yet" after a
  // successful generation, because nothing invalidated it.
  const wasRunning = useRef(false)
  useEffect(() => {
    if (running) {
      wasRunning.current = true
      return
    }
    if (wasRunning.current) {
      wasRunning.current = false
      queryClient.invalidateQueries({ queryKey: ['documents', organizationId] })
    }
  }, [running, organizationId, queryClient])

  // A one-second clock for the elapsed time, only while a run is live.
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    if (!running) return
    const timer = window.setInterval(() => setNow(Date.now()), 1000)
    return () => window.clearInterval(timer)
  }, [running])

  const [dismissed, setDismissed] = useState<string | null>(null)
  const [expanded, setExpanded] = useState(true)

  // Every out-of-scope citation across the run, deduplicated. An administrator
  // cares about the set, not about which document mentioned the same control.
  const outOfScope = useMemo(
    () =>
      Array.from(
        new Set((status?.results ?? []).flatMap((r) => r.out_of_scope_citations ?? [])),
      ).sort(),
    [status?.results],
  )

  if (!status || status.status === 'idle') return null
  const runKey = status.finished_at ?? status.updated_at ?? status.status
  if (!running && dismissed === runKey) return null

  const { title, tone } = headline(status)
  const items = status.items ?? []
  const total = status.total ?? items.length
  const done = items.length
    ? items.filter((i) => i.state !== 'pending' && i.state !== 'running').length
    : status.completed ?? 0
  const pct = total ? Math.round((done / total) * 100) : 0
  const current = running && status.current_index != null ? items[status.current_index] : undefined
  const stageIndex = STAGES.findIndex((s) => s.key === status.stage)
  const legacy = items.length === 0 ? parseLegacyMessage(status.message) : {}
  const nowName = current?.display_name ?? legacy.name
  const nowScope = current?.domain_id || legacy.scope
  const nowDetail = status.detail ?? legacy.detail ?? status.message

  const started = status.started_at ? Date.parse(status.started_at) : NaN
  const ended = status.finished_at ? Date.parse(status.finished_at) : running ? now : NaN
  const elapsed = Number.isFinite(started) && Number.isFinite(ended) ? ended - started : null
  // Average over finished documents; unchanged ones are quick, so this leans
  // optimistic early in a run and settles as it goes.
  const remaining =
    running && elapsed != null && done > 0 && total > done ? (elapsed / done) * (total - done) : null
  const conflicts = items.reduce((n, i) => n + (i.conflict_count ?? 0), 0)

  return (
    <section className={`docgen-run is-${tone}`} aria-live="polite" aria-label="Document generation status">
      <header className="docgen-run-head">
        <span className="docgen-run-icon" aria-hidden="true">
          {tone === 'running' ? <span className="docgen-run-spinner" /> : tone === 'success' ? '✓' : '!'}
        </span>
        <div className="docgen-run-heading">
          <h3>{title}</h3>
          <p>
            {total > 0 && <span>{done} of {total} documents</span>}
            {elapsed != null && <span>{formatDuration(elapsed)} {running ? 'elapsed' : 'total'}</span>}
            {remaining != null && <span>about {formatDuration(remaining)} left</span>}
          </p>
        </div>
        <span className="docgen-run-pct">{pct}%</span>
        {!running && (
          <button
            type="button"
            className="docgen-run-dismiss"
            aria-label="Dismiss run summary"
            onClick={() => setDismissed(runKey)}
          >
            ×
          </button>
        )}
      </header>

      {/* One segment per document, coloured by outcome. */}
      {items.length > 0 ? (
        <div className="docgen-run-bar" role="progressbar" aria-valuemin={0} aria-valuemax={total} aria-valuenow={done}>
          {items.map((item, i) => (
            <span
              key={`${item.generator}-${item.domain_id}-${i}`}
              className={`docgen-run-seg is-${item.state}`}
              title={`${item.display_name}${item.domain_id ? ` · ${item.domain_id}` : ''} — ${STATE_LABEL[item.state]}`}
            />
          ))}
        </div>
      ) : (
        <div className="docgen-run-bar is-single" role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={pct}>
          <span className={`docgen-run-seg ${running ? 'is-running' : 'is-created'}`} style={{ flexBasis: `${running ? Math.max(pct, 8) : 100}%` }} />
        </div>
      )}

      {running && (
        <div className="docgen-run-now">
          <div className="docgen-run-now-text">
            <strong>
              {nowName ?? 'Starting'}
              {nowScope && <span className="docgen-run-scope">{nowScope}</span>}
            </strong>
            <span>{nowDetail ?? 'Waiting for the worker…'}</span>
          </div>
          <ol className="docgen-run-stages" aria-label="Stage of the current document">
            {STAGES.map((s, i) => (
              <li
                key={s.key}
                className={i < stageIndex ? 'is-done' : i === stageIndex ? 'is-active' : ''}
              >
                {s.label}
              </li>
            ))}
          </ol>
        </div>
      )}

      {!running && status.status === 'failed' && status.error && (
        <p className="docgen-run-note is-danger">{status.error}</p>
      )}

      {items.length > 0 && (
        <div className="docgen-run-stats">
          <span className="docgen-run-stat is-created">{status.generated ?? 0} generated</span>
          <span className="docgen-run-stat is-skipped">{status.skipped ?? 0} unchanged</span>
          {(status.failed ?? 0) > 0 && (
            <span className="docgen-run-stat is-failed">{status.failed} refused</span>
          )}
          <button type="button" className="docgen-run-toggle" onClick={() => setExpanded((v) => !v)} aria-expanded={expanded}>
            {expanded ? 'Hide documents' : `Show ${items.length} documents`}
          </button>
        </div>
      )}

      {status.status === 'partial' && (
        <p className="docgen-run-note is-warning">
          Stopped after {done} of {total}.{' '}
          {status.failed ? 'The rest were not attempted' : 'Nothing failed'}; run it again to finish them.
        </p>
      )}
      {conflicts > 0 && !running && (
        <p className="docgen-run-note is-warning">
          {conflicts} section{conflicts === 1 ? '' : 's'} need your decision — a regeneration changed text you had edited.
        </p>
      )}
      {outOfScope.length > 0 && (
        <p className="docgen-run-note is-danger">
          Cites {outOfScope.length} control{outOfScope.length === 1 ? '' : 's'} outside your scope:{' '}
          {outOfScope.join(', ')}. Review before approving.
        </p>
      )}

      {expanded && items.length > 0 && (
        <ul className="docgen-run-items">
          {items.map((item, i) => {
            const openable = Boolean(onOpenDocument && item.document_id && item.state !== 'failed')
            const body = (
              <>
                <span className={`docgen-run-dot is-${item.state}`} aria-hidden="true" />
                <span className="docgen-run-item-name">
                  {item.title || item.display_name}
                  {item.domain_id && !item.title && <span className="docgen-run-scope">{item.domain_id}</span>}
                </span>
                <span className="docgen-run-item-state">
                  {item.state === 'running' && i === status.current_index && stageIndex >= 0
                    ? STAGES[stageIndex].label
                    : STATE_LABEL[item.state]}
                </span>
                {item.error && <span className="docgen-run-item-error">{item.error}</span>}
              </>
            )
            return (
              <li key={`${item.generator}-${item.domain_id}-${i}`} className={`docgen-run-item is-${item.state}`}>
                {openable ? (
                  <button type="button" onClick={() => onOpenDocument!(item.document_id!)}>{body}</button>
                ) : (
                  <div>{body}</div>
                )}
              </li>
            )
          })}
        </ul>
      )}
    </section>
  )
}
