import { useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { reviewKeys, type ReviewViewer } from '../../data/engagementReviewApi'
import { useModalDismiss } from '../../hooks/useModalDismiss'
import {
  createEngagementQuery,
  getEngagementQuery,
  listEngagementQueries,
  respondToEngagementQuery,
  updateEngagementQueryStatus,
  type EngagementQuery,
  type EngagementQueryStatus,
} from '../../data/apiClient'
import { cancelOnEscape, errorMessage, fmtDateTime } from './ReviewCardShared'
import './engagement-review-tokens.css'
import './ReviewQueries.css'

/**
 * PROPS CONTRACT written by the lead. The card panel uses it for an evidence
 * object, and ReviewBoard (helper e) uses it for a control that has no
 * evidence object. Keep this shape.
 */
export type ReviewQueryTarget =
  | { evidenceId: string; scfId?: undefined }
  | { scfId: string; evidenceId?: undefined }

export interface ReviewQueriesProps {
  organizationId: string
  engagementId: string
  /** Exactly one target: an evidence object, or a control. */
  target: ReviewQueryTarget
  viewer: ReviewViewer
  /** Called after a query is raised, answered, closed or reopened. */
  onChanged?: () => void
  /**
   * Rendered inside a modal dialog. The dialog owns Escape through the
   * dismiss stack, so the new-query form registers on top of it: Escape then
   * closes the form and keeps the dialog open.
   */
  inDialog?: boolean
}

const STATUS_LABELS: Record<EngagementQueryStatus, string> = {
  open: 'Open',
  answered: 'Answered',
  closed: 'Closed',
}

/**
 * The auditor's question log for one target. Raise a query (title and body),
 * open one to read it, answer it, close it, reopen it. The server enforces the
 * lifecycle; a refusal is shown in its own words. Raising a query never moves
 * the evidence card, so after raising one on an evidence object the auditor is
 * told so.
 */
export default function ReviewQueries({
  organizationId, engagementId, target, viewer, onChanged, inDialog = false,
}: ReviewQueriesProps) {
  const queryClient = useQueryClient()
  const targetKey = target.evidenceId !== undefined ? `evidence:${target.evidenceId}` : `control:${target.scfId}`
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [showNew, setShowNew] = useState(false)
  const [form, setForm] = useState({ title: '', body: '' })
  const [reply, setReply] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [raisedPrompt, setRaisedPrompt] = useState(false)
  useModalDismiss(inDialog && showNew, () => setShowNew(false))

  const list = useQuery({
    queryKey: reviewKeys.queries(engagementId, targetKey),
    queryFn: () => listEngagementQueries(
      organizationId, engagementId,
      target.evidenceId !== undefined ? { evidenceId: target.evidenceId } : { scfId: target.scfId },
    ),
  })

  const detail = useQuery({
    queryKey: [...reviewKeys.queries(engagementId, targetKey), 'detail', selectedId],
    queryFn: () => getEngagementQuery(organizationId, engagementId, selectedId as string),
    enabled: selectedId !== null,
  })

  // Display names only: an email address or a user id is never rendered
  // (an external auditor's responses carry no email at all).
  const nameFor = (name: string | null): string => name?.trim() || 'Unnamed user'

  const afterWrite = async () => {
    await queryClient.invalidateQueries({ queryKey: reviewKeys.all(engagementId) })
    onChanged?.()
  }

  const raise = async (e: React.FormEvent) => {
    e.preventDefault()
    setError(null)
    if (!form.title.trim() || !form.body.trim()) {
      setError('A query needs a title and a question.')
      return
    }
    setBusy(true)
    try {
      const title = form.title.trim()
      const body = form.body.trim()
      await createEngagementQuery(
        organizationId, engagementId,
        target.evidenceId !== undefined
          ? { evidence_id: target.evidenceId, title, body }
          : { scf_id: target.scfId, title, body },
      )
      setForm({ title: '', body: '' })
      setShowNew(false)
      setRaisedPrompt(target.evidenceId !== undefined && viewer.is_auditor)
      await afterWrite()
    } catch (err) {
      setError(errorMessage(err, 'The query could not be raised.'))
    } finally { setBusy(false) }
  }

  const answer = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!selectedId) return
    setError(null)
    if (!reply.trim()) {
      setError('Write a response first.')
      return
    }
    setBusy(true)
    try {
      await respondToEngagementQuery(organizationId, engagementId, selectedId, reply.trim())
      setReply('')
      await afterWrite()
    } catch (err) {
      setError(errorMessage(err, 'The response could not be posted.'))
    } finally { setBusy(false) }
  }

  const setStatus = async (status: EngagementQueryStatus) => {
    if (!selectedId) return
    setBusy(true); setError(null)
    try {
      await updateEngagementQueryStatus(organizationId, engagementId, selectedId, status)
      await afterWrite()
    } catch (err) {
      setError(errorMessage(err, 'The status could not be changed.'))
    } finally { setBusy(false) }
  }

  const q: EngagementQuery | undefined = detail.data

  return (
    <div className="erq">
      {raisedPrompt && (
        <div className="erq-prompt" role="status">
          <p>This did not change the card's stage. Set it to Action required if the organisation needs to act.</p>
          <button type="button" className="erq-btn-link" onClick={() => setRaisedPrompt(false)}>Dismiss</button>
        </div>
      )}

      {error && <p className="erq-error" role="alert">{error}</p>}

      {selectedId ? (
        <div className="erq-detail">
          <button type="button" className="erq-btn-link" onClick={() => { setSelectedId(null); setReply(''); setError(null) }}>
            <span aria-hidden="true">←</span> All queries
          </button>
          {detail.isPending && <p className="erq-muted">Loading query…</p>}
          {detail.isError && (
            <p className="erq-error" role="alert">{errorMessage(detail.error, 'The query could not be loaded.')}</p>
          )}
          {q && (
            <>
              <div className="erq-query">
                <div className="erq-query-head">
                  <strong className="erq-query-title">{q.title}</strong>
                  <span className={`erq-status erq-status-${q.status}`}>{STATUS_LABELS[q.status]}</span>
                </div>
                <p className="erq-meta">
                  Raised by {nameFor(q.raised_by_name)}
                  {q.created_at ? ` · ${fmtDateTime(q.created_at)}` : ''}
                </p>
                <p className="erq-body">{q.body}</p>
              </div>

              <ol className="erq-responses" aria-label="Responses">
                {(q.responses ?? []).length === 0 && <li className="erq-muted">No responses yet.</li>}
                {(q.responses ?? []).map(r => (
                  <li key={r.id} className="erq-response">
                    <p className="erq-meta">{nameFor(r.name)} ·{fmtDateTime(r.created_at)}</p>
                    <p className="erq-body">{r.content}</p>
                  </li>
                ))}
              </ol>

              {q.status !== 'closed' && (
                <form className="erq-form" onSubmit={answer} noValidate>
                  <label className="erq-label" htmlFor={`erq-reply-${q.id}`}>Answer</label>
                  <textarea
                    id={`erq-reply-${q.id}`}
                    className="erq-textarea"
                    rows={3}
                    value={reply}
                    onChange={e => setReply(e.target.value)}
                  />
                  <div className="erq-actions">
                    <button type="submit" className="erq-btn erq-btn-primary" disabled={busy}>Post answer</button>
                    {viewer.is_auditor && (
                      <button type="button" className="erq-btn" disabled={busy} onClick={() => void setStatus('closed')}>
                        Close query
                      </button>
                    )}
                  </div>
                </form>
              )}
              {q.status === 'closed' && (
                <div className="erq-actions">
                  <span className="erq-muted">Closed{q.closed_at ? ` ${fmtDateTime(q.closed_at)}` : ''}.</span>
                  {viewer.is_auditor && (
                    <button type="button" className="erq-btn" disabled={busy} onClick={() => void setStatus('open')}>
                      Reopen query
                    </button>
                  )}
                </div>
              )}
            </>
          )}
        </div>
      ) : (
        <>
          <p className="erq-muted erq-hint">
            {target.evidenceId !== undefined
              ? 'A query is a tracked question. It does not move the card: set Action required if the organisation must act.'
              : 'A query is a tracked question about this control.'}
            {' '}Open → Answered when someone answers → Closed when settled.
          </p>
          {list.isPending && <p className="erq-muted">Loading queries…</p>}
          {list.isError && (
            <p className="erq-error" role="alert">{errorMessage(list.error, 'Queries could not be loaded.')}</p>
          )}
          {list.data && list.data.length === 0 && <p className="erq-muted">No queries raised.</p>}
          {list.data && list.data.length > 0 && (
            <ul className="erq-list">
              {list.data.map(item => (
                <li key={item.id}>
                  <button
                    type="button"
                    className="erq-item"
                    onClick={() => { setSelectedId(item.id); setError(null); setRaisedPrompt(false) }}
                  >
                    <span className="erq-item-title">{item.title}</span>
                    <span className={`erq-status erq-status-${item.status}`}>{STATUS_LABELS[item.status]}</span>
                    <span className="erq-count">
                      {item.response_count} response{item.response_count === 1 ? '' : 's'}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          )}

          {showNew ? (
            <form
              className="erq-form"
              onSubmit={raise}
              noValidate
              aria-label="Raise a query"
              onKeyDown={cancelOnEscape(() => setShowNew(false))}
            >
              <label className="erq-label" htmlFor={`erq-title-${targetKey}`}>Title</label>
              <input
                id={`erq-title-${targetKey}`}
                className="erq-input"
                value={form.title}
                onChange={e => setForm(f => ({ ...f, title: e.target.value }))}
                autoFocus
              />
              <label className="erq-label" htmlFor={`erq-body-${targetKey}`}>Question</label>
              <textarea
                id={`erq-body-${targetKey}`}
                className="erq-textarea"
                rows={3}
                value={form.body}
                onChange={e => setForm(f => ({ ...f, body: e.target.value }))}
              />
              <div className="erq-actions">
                <button type="submit" className="erq-btn erq-btn-primary" disabled={busy}>Raise query</button>
                <button type="button" className="erq-btn" onClick={() => { setShowNew(false); setError(null) }}>Cancel</button>
              </div>
            </form>
          ) : (
            <button type="button" className="erq-btn erq-raise" onClick={() => { setShowNew(true); setRaisedPrompt(false) }}>
              Raise a query
            </button>
          )}
        </>
      )}
    </div>
  )
}
