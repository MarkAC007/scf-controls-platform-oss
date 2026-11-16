import { useEffect } from 'react'
import type React from 'react'
import { useQuery } from '@tanstack/react-query'
import type { ReviewFocus, ReviewTotals } from './EngagementReviewWorkspace'
import {
  getReviewDashboard,
  reviewKeys,
  REVIEW_STATUSES,
  REVIEW_STATUS_LABELS,
  type ReviewDashboard,
  type ReviewStatus,
  type StatusBlock,
  type TimelineItem,
} from '../../data/engagementReviewApi'
import './EngagementDashboard.css'

/**
 * PROPS CONTRACT written by the lead. Helper (f) replaces the body of this
 * file with the real dashboard; the exported props must keep this shape
 * because EngagementReviewWorkspace renders the dashboard with them.
 */
export interface EngagementDashboardProps {
  organizationId: string
  engagementId: string
  framework: string
  frameworkLabel: string
  /** Switch to the board and open a card, or a control's query form. */
  onOpenOnBoard: (focus: ReviewFocus) => void
  /** Report the header totals (evidence under test, controls in scope). */
  onTotals: (totals: ReviewTotals) => void
}

/**
 * The engagement dashboard: testing progress for one framework tab.
 *
 * Every number comes from the review-dashboard response. The auditor tests
 * evidence objects; controls and clauses are rolled up from them by the
 * server, so the browser only lays the counts out (and turns a count into a
 * percentage of the server's own total).
 */

/** Stage → class suffix; the suffix picks the --erb-* stage tokens in CSS. */
const STAGE_CLASS: Record<ReviewStatus, string> = {
  open: 'open',
  in_progress: 'progress',
  action_required: 'action',
  submitted: 'submitted',
  accepted: 'accepted',
}

const stageClass = (status: ReviewStatus) => `erd-stage-${STAGE_CLASS[status]}`

const plural = (n: number, one: string, many: string) => `${n} ${n === 1 ? one : many}`

/** A date-only value is a calendar date; parse it as local so it does not shift a day. */
function formatDate(value: string): string {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value)
  const d = m ? new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3])) : new Date(value)
  if (Number.isNaN(d.getTime())) return value
  return d.toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric' })
}

/** The window dates come from the engagement record; the others are reached by progress. */
const DATE_FROM_ENGAGEMENT = new Set(['window_start', 'window_end'])

function timelineDate(item: TimelineItem): string {
  if (item.date) return formatDate(item.date)
  return DATE_FROM_ENGAGEMENT.has(item.key) ? 'not set' : 'not yet'
}

function timelineProgress(item: TimelineItem, data: ReviewDashboard): string | null {
  if (item.done) return null
  if (item.key === 'all_evidence_tested') {
    return `${data.evidence.by_status.accepted} of ${data.evidence.total} accepted so far`
  }
  if (item.key === 'all_clauses_accepted') {
    return `${data.clauses.by_status.accepted} of ${data.clauses.total} so far`
  }
  return null
}

function percent(part: number, total: number): string {
  return total > 0 ? `${Math.round((100 * part) / total)}%` : '—'
}

interface TileProps {
  label: string
  value: string
  sub: string
  tone: 'accepted' | 'action' | 'progress'
}

function Tile({ label, value, sub, tone }: TileProps) {
  return (
    <div className={`erd-tile erd-tile-${tone}`}>
      <span className="erd-tile-label">{label}</span>
      <span className="erd-tile-value">{value}</span>
      <span className="erd-tile-sub">{sub}</span>
    </div>
  )
}

interface StageBarProps {
  id: string
  label: string
  block: StatusBlock
  noun: [string, string]
}

function StageBar({ id, label, block, noun }: StageBarProps) {
  const summary = REVIEW_STATUSES
    .map(s => `${REVIEW_STATUS_LABELS[s]} ${block.by_status[s]}`)
    .join(', ')
  return (
    <div className="erd-bar-row" data-testid={`erd-bar-${id}`}>
      <div className="erd-bar-head">
        <span className="erd-bar-label">{label}</span>
        <span className="erd-bar-total">{plural(block.total, noun[0], noun[1])}</span>
      </div>
      {block.total > 0 ? (
        <div className="erd-bar" role="img" aria-label={`${label}: ${summary}`}>
          {REVIEW_STATUSES.filter(s => block.by_status[s] > 0).map(s => (
            <div
              key={s}
              className={`erd-bar-seg ${stageClass(s)}`}
              style={{ '--erd-seg': block.by_status[s] } as React.CSSProperties}
              aria-hidden="true"
            >{block.by_status[s]}</div>
          ))}
        </div>
      ) : (
        <p className="erd-bar-empty">Nothing to show: no {noun[1]} for this framework.</p>
      )}
      <ul className="erd-legend" aria-label={`${label} by stage`}>
        {REVIEW_STATUSES.map(s => (
          <li key={s} className={`erd-legend-item ${stageClass(s)}`} data-stage={s}>
            <span className="erd-legend-swatch" aria-hidden="true" />
            {REVIEW_STATUS_LABELS[s]} <span className="erd-legend-count">{block.by_status[s]}</span>
          </li>
        ))}
      </ul>
    </div>
  )
}

export default function EngagementDashboard({
  organizationId, engagementId, framework, frameworkLabel, onOpenOnBoard, onTotals,
}: EngagementDashboardProps) {
  const query = useQuery({
    queryKey: reviewKeys.dashboard(engagementId, framework),
    queryFn: () => getReviewDashboard(organizationId, engagementId, framework),
  })
  const data = query.data

  useEffect(() => {
    if (data) onTotals({ evidence: data.evidence.total, controls: data.controls.total })
  }, [data, onTotals])

  if (query.isPending) {
    return <p className="erd-state" role="status">Loading the dashboard…</p>
  }

  if (query.isError || !data) {
    const message = query.error instanceof Error ? query.error.message : 'The dashboard could not be loaded.'
    return (
      <div className="erd-state erd-state-error" role="alert">
        <p className="erd-state-message">Could not load the dashboard: {message}</p>
        <button
          type="button" className="erd-retry"
          onClick={() => { void query.refetch() }}
          disabled={query.isFetching}
        >{query.isFetching ? 'Retrying…' : 'Retry'}</button>
      </div>
    )
  }

  const { evidence, controls, clauses, open_queries: queries } = data
  const isEmpty = evidence.total === 0 && controls.total === 0 && clauses.total === 0

  return (
    <div className="erd">
      {isEmpty && (
        <p className="erd-empty-banner" role="status">
          Nothing is in scope for {frameworkLabel} in this engagement yet, so there is no evidence to test.
        </p>
      )}

      <div className="erd-tiles">
        <Tile
          tone="accepted" label="Evidence accepted"
          value={`${evidence.by_status.accepted} / ${evidence.total}`}
          sub="evidence objects passed testing"
        />
        <Tile
          tone="accepted" label="Clauses accepted"
          value={percent(clauses.by_status.accepted, clauses.total)}
          sub={`${clauses.by_status.accepted} of ${clauses.total} ${frameworkLabel} clauses`}
        />
        <Tile
          tone="accepted" label="Controls accepted"
          value={`${controls.by_status.accepted} / ${controls.total}`}
          sub="SCF controls, rolled up"
        />
        <Tile
          tone="action" label="Action required"
          value={String(evidence.by_status.action_required)}
          sub="evidence objects with the organisation"
        />
        <Tile
          tone="progress" label="Open queries"
          value={String(queries.total)}
          sub={`${queries.evidence} on evidence · ${queries.control} on controls`}
        />
      </div>

      <section className="erd-card" aria-labelledby="erd-progress-title">
        <div>
          <h2 id="erd-progress-title" className="erd-h2">Testing progress</h2>
          <p className="erd-intro">
            The auditor tests evidence objects. Controls and clauses roll up from them: a control is
            accepted when every evidence object mapped to it is accepted, and one object needing action
            holds the control and every clause it maps to. A control with no evidence object mapped stays
            open until the auditor resolves it by query.
          </p>
        </div>
        <StageBar id="evidence" label="Evidence objects (tested)" block={evidence} noun={['object', 'objects']} />
        <StageBar
          id="controls" label="SCF controls (rolled up from evidence)" block={controls}
          noun={['control', 'controls']}
        />
        <StageBar
          id="clauses" label={`${frameworkLabel} clauses (rolled up from controls)`} block={clauses}
          noun={['clause', 'clauses']}
        />
      </section>

      <div className="erd-columns">
        <section className="erd-card erd-coverage" aria-labelledby="erd-coverage-title">
          <div>
            <h2 id="erd-coverage-title" className="erd-h2">{frameworkLabel} — clause coverage</h2>
            <p className="erd-intro">
              Each tile is a clause, coloured by the rolled-up stage of the evidence behind it. The counts
              are the SCF controls and evidence objects that map to it.
            </p>
          </div>
          {data.groups.length === 0 ? (
            <p className="erd-none">No clauses of {frameworkLabel} map to the controls in scope.</p>
          ) : data.groups.map(group => {
            const items = data.clause_rollup.filter(c => c.group === group.key)
            return (
              <div key={group.key} className="erd-group" data-testid={`erd-group-${group.key}`}>
                <div className="erd-group-head">
                  <span className="erd-group-label">Section {group.label}</span>
                  <span className="erd-group-summary">
                    {group.clauses_accepted} of {group.clauses_total} clauses accepted
                  </span>
                </div>
                <ul className="erd-clauses" aria-label={`Section ${group.label} clauses`}>
                  {items.map(c => {
                    const stage = REVIEW_STATUS_LABELS[c.status]
                    return (
                      <li
                        key={c.clause}
                        className={`erd-clause ${stageClass(c.status)}`}
                        data-stage={c.status}
                        title={`${c.clause}: ${stage}, ${plural(c.control_count, 'control', 'controls')}, ${plural(c.evidence_count, 'evidence object', 'evidence objects')}`}
                      >
                        <span className="erd-clause-id">{c.clause}</span>
                        <span className="erd-clause-stage">{stage}</span>
                        <span className="erd-clause-counts">{c.control_count} ctrl · {c.evidence_count} ev</span>
                      </li>
                    )
                  })}
                </ul>
              </div>
            )
          })}
        </section>

        <div className="erd-side">
          <section className="erd-card" aria-labelledby="erd-action-title">
            <h2 id="erd-action-title" className="erd-h2">Evidence needing the organisation</h2>
            {data.action_required.length === 0 ? (
              <p className="erd-none">Nothing is waiting on the organisation.</p>
            ) : (
              <ul className="erd-list">
                {data.action_required.map(item => (
                  <li key={item.evidence_id}>
                    <button
                      type="button" className="erd-row erd-row-action"
                      onClick={() => onOpenOnBoard({ evidenceId: item.evidence_id })}
                    >
                      <span className="erd-row-head">
                        <span className="erd-row-id">{item.evidence_id}</span>
                        <span className="erd-row-title">{item.title ?? 'Untitled evidence object'}</span>
                      </span>
                      <span className="erd-row-meta">
                        Holds SCF {item.held_controls.length ? item.held_controls.join(', ') : 'none'}
                        {' · '}{frameworkLabel} {item.held_clauses.length ? item.held_clauses.join(', ') : 'none'}
                        {' · '}{plural(item.open_query_count, 'open query', 'open queries')}
                      </span>
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </section>

          <section className="erd-card" aria-labelledby="erd-orphans-title">
            <h2 id="erd-orphans-title" className="erd-h2">Controls with no evidence object</h2>
            {data.no_evidence_controls.length === 0 ? (
              <p className="erd-none">Every in-scope control has an evidence object.</p>
            ) : (
              <>
                <p className="erd-intro">
                  Nothing in the catalog maps to these, so there is nothing to test. Each stays Open until the
                  auditor resolves it with a control-level query.
                </p>
                <ul className="erd-list">
                  {data.no_evidence_controls.map(c => (
                    <li key={c.scf_id}>
                      <button
                        type="button" className="erd-row erd-row-orphan"
                        onClick={() => onOpenOnBoard({ controlScfId: c.scf_id })}
                      >
                        <span className="erd-row-head">
                          <span className="erd-row-id erd-row-control">{c.scf_id}</span>
                          <span className="erd-row-title">{c.title ?? 'Untitled control'}</span>
                        </span>
                        <span className="erd-row-meta">
                          {frameworkLabel} {c.clauses.length ? c.clauses.join(', ') : 'none'}
                          {' · '}{plural(c.open_query_count, 'open query', 'open queries')}
                        </span>
                      </button>
                    </li>
                  ))}
                </ul>
              </>
            )}
          </section>

          <section className="erd-card" aria-labelledby="erd-timeline-title">
            <h2 id="erd-timeline-title" className="erd-h2">Engagement timeline</h2>
            {data.timeline.length === 0 ? (
              <p className="erd-none">No milestones for this engagement.</p>
            ) : (
              <ol className="erd-timeline">
                {data.timeline.map(item => {
                  const progress = timelineProgress(item, data)
                  return (
                    <li
                      key={item.key}
                      className={`erd-milestone${item.done ? ' is-done' : ''}`}
                      data-testid={`erd-milestone-${item.key}`}
                    >
                      <span className="erd-milestone-dot" aria-hidden="true" />
                      <span className="erd-milestone-text">
                        <span className="erd-milestone-label">
                          {item.label}
                          <span className="erd-visually-hidden">{item.done ? ' (done)' : ' (not done)'}</span>
                        </span>
                        <span className="erd-milestone-sub">
                          {timelineDate(item)}{progress ? ` · ${progress}` : ''}
                        </span>
                      </span>
                    </li>
                  )
                })}
              </ol>
            )}
          </section>
        </div>
      </div>
    </div>
  )
}
