import { useEffect, useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import {
  changeReviewStatus,
  getReviewCard,
  reviewKeys,
  REVIEW_STATUS_LABELS,
  type EvidenceCardDetail,
  type ReviewStatus,
  type ReviewViewer,
} from '../../data/engagementReviewApi'
import { EvidenceFileUpload } from '../evidence/EvidenceFileUpload'
import { cancelOnEscape, errorMessage, errorStatus } from './ReviewCardShared'

/**
 * Stage buttons for one evidence card, and the organisation's response form.
 *
 * Only the moves the server lists in `allowed_transitions` are rendered. A move
 * whose `note_required[target]` is true opens an inline note first. When the
 * card is in Action required and the caller may submit, the organisation
 * answers through a form that links files (existing or newly uploaded) or
 * states why none is linked.
 */

const MOVE_LABELS: Record<ReviewStatus, string> = {
  open: 'Move back to Open',
  in_progress: 'Start testing',
  action_required: 'Action required',
  submitted: 'Mark as submitted',
  accepted: 'Accept evidence',
}

const NOTE_LABELS: Record<ReviewStatus, string> = {
  open: 'Why is this card going back to Open?',
  in_progress: 'Note for this move',
  action_required: 'What does the organisation need to do? They will read this note.',
  submitted: 'Note for the auditor',
  accepted: 'Acceptance note',
}

interface StageActionsProps {
  organizationId: string
  engagementId: string
  framework: string
  card: EvidenceCardDetail
  viewer: ReviewViewer
}

interface ReviewCardStageActionsProps extends StageActionsProps {
  /** A move begun on the board that needs a note: its form opens straight away. */
  pendingMove?: ReviewStatus | null
  /** Called once `pendingMove` has been taken, so it is not taken twice. */
  onPendingMoveConsumed?: () => void
}

/** Whether a move to `to` is made through the inline note form of the stage buttons. */
function needsNoteForm(card: EvidenceCardDetail, to: ReviewStatus): boolean {
  if (!card.allowed_transitions.includes(to)) return false
  const responseForm = card.status === 'action_required' && card.allowed_transitions.includes('submitted')
  if (responseForm && to === 'submitted') return false
  return !!card.note_required[to] || to === 'action_required'
}

function nextMoveText(card: EvidenceCardDetail): string {
  switch (card.status) {
    case 'action_required':
      return `The next move is the organisation's: ${card.owner.label} to respond.`
    case 'accepted':
      return 'The auditor has accepted this evidence. Only the auditor can reopen it.'
    default:
      return card.reviewer
        ? `The next move is the auditor's: ${card.reviewer.name} is reviewing.`
        : "The next move is the auditor's. No reviewer is assigned yet."
  }
}

export default function ReviewCardStageActions({
  organizationId, engagementId, framework, card, viewer, pendingMove = null, onPendingMoveConsumed,
}: ReviewCardStageActionsProps) {
  const queryClient = useQueryClient()
  const [pending, setPending] = useState<ReviewStatus | null>(() =>
    pendingMove && needsNoteForm(card, pendingMove) ? pendingMove : null)
  const [note, setNote] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const showResponseForm = card.status === 'action_required' && card.allowed_transitions.includes('submitted')
  const moves = card.allowed_transitions.filter(t => !(showResponseForm && t === 'submitted'))

  // Take a move handed over by the board: open its note form, then clear it.
  // A move to Submitted from Action required is answered by the response form,
  // which is already open.
  useEffect(() => {
    if (!pendingMove) return
    if (needsNoteForm(card, pendingMove)) {
      setPending(pendingMove)
      setNote('')
      setError(null)
    }
    onPendingMoveConsumed?.()
  }, [pendingMove, card, onPendingMoveConsumed])

  const refresh = () => queryClient.invalidateQueries({ queryKey: reviewKeys.all(engagementId) })

  const move = async (to: ReviewStatus, withNote?: string) => {
    setBusy(true)
    setError(null)
    try {
      await changeReviewStatus(organizationId, engagementId, card.evidence_id, framework, {
        to_status: to,
        from_status: card.status,
        ...(withNote ? { note: withNote } : {}),
      })
      setPending(null)
      setNote('')
      await refresh()
    } catch (err) {
      setError(errorMessage(err, 'The card could not be moved.'))
      // 409: someone else moved the card; show where it is now.
      if (errorStatus(err) === 409) await refresh()
    } finally {
      setBusy(false)
    }
  }

  const onMoveClick = (to: ReviewStatus) => {
    setError(null)
    if (card.note_required[to] || to === 'action_required') {
      setPending(to)
      setNote('')
      return
    }
    void move(to)
  }

  const onNoteSubmit = (e: React.FormEvent) => {
    e.preventDefault()
    if (!pending) return
    if (!note.trim()) {
      setError('A note is required for this move.')
      return
    }
    void move(pending, note.trim())
  }

  return (
    <section className="erp-section erp-actions" aria-label="Review stage">
      {moves.length === 0 && !showResponseForm && (
        <p className="erp-next-move">{nextMoveText(card)}</p>
      )}

      {moves.length > 0 && (
        <div className="erp-move-row">
          {moves.map(to => (
            <button
              key={to}
              type="button"
              className={`erp-move erp-move-${to}${pending === to ? ' is-pending' : ''}`}
              aria-expanded={card.note_required[to] || to === 'action_required' ? pending === to : undefined}
              onClick={() => onMoveClick(to)}
              disabled={busy}
            >
              {MOVE_LABELS[to]}
            </button>
          ))}
        </div>
      )}

      {pending && (
        <form className="erp-note-form" onSubmit={onNoteSubmit} noValidate onKeyDown={cancelOnEscape(() => setPending(null))}>
          <label className="erp-label" htmlFor="erp-move-note">
            {NOTE_LABELS[pending]} <span className="erp-required">(required)</span>
          </label>
          <textarea
            id="erp-move-note"
            className="erp-textarea"
            rows={3}
            aria-required="true"
            value={note}
            onChange={e => setNote(e.target.value)}
            autoFocus
          />
          <div className="erp-form-actions">
            <button type="submit" className="erp-btn erp-btn-primary" disabled={busy}>
              {MOVE_LABELS[pending]}
            </button>
            <button type="button" className="erp-btn" onClick={() => { setPending(null); setError(null) }}>
              Cancel
            </button>
          </div>
        </form>
      )}

      {error && <p className="erp-error" role="alert">{error}</p>}

      {showResponseForm && (
        <ResponseForm
          organizationId={organizationId}
          engagementId={engagementId}
          framework={framework}
          card={card}
          viewer={viewer}
        />
      )}
    </section>
  )
}

// ---------------------------------------------------------------------------
// Organisation response (Action required → Submitted)
// ---------------------------------------------------------------------------

function ResponseForm({ organizationId, engagementId, framework, card }: StageActionsProps) {
  const queryClient = useQueryClient()
  const [note, setNote] = useState('')
  const [picked, setPicked] = useState<Set<string>>(new Set())
  const [noneApplies, setNoneApplies] = useState(false)
  const [reason, setReason] = useState('')
  const [showUpload, setShowUpload] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const toggle = (id: string) => {
    setPicked(prev => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
    setNoneApplies(false)
  }

  // After an upload, fetch the card again and tick the file that was not there before.
  const onUploadComplete = async () => {
    const before = new Set(card.artifacts.map(a => a.id))
    try {
      const fresh = await queryClient.fetchQuery({
        queryKey: reviewKeys.card(engagementId, card.evidence_id, framework),
        queryFn: () => getReviewCard(organizationId, engagementId, card.evidence_id, framework),
        staleTime: 0,
      })
      const added = fresh.artifacts.filter(a => !before.has(a.id)).map(a => a.id)
      if (added.length) {
        setPicked(prev => new Set([...prev, ...added]))
        setNoneApplies(false)
      }
      setShowUpload(false)
    } catch (err) {
      setError(errorMessage(err, 'The file was uploaded but the card could not be reloaded.'))
    }
    await queryClient.invalidateQueries({ queryKey: reviewKeys.all(engagementId) })
  }

  const onSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    setError(null)
    if (!note.trim()) {
      setError('Write a note for the auditor.')
      return
    }
    const ids = card.artifacts.filter(a => picked.has(a.id)).map(a => a.id)
    if (ids.length === 0 && !(noneApplies && reason.trim())) {
      setError('Link at least one file, upload one, or say why no file is linked.')
      return
    }
    setBusy(true)
    try {
      await changeReviewStatus(organizationId, engagementId, card.evidence_id, framework, {
        to_status: 'submitted',
        from_status: card.status,
        note: note.trim(),
        ...(ids.length ? { artifact_file_ids: ids } : { no_artifact_reason: reason.trim() }),
      })
      setNote('')
      setPicked(new Set())
      setReason('')
      setNoneApplies(false)
      await queryClient.invalidateQueries({ queryKey: reviewKeys.all(engagementId) })
    } catch (err) {
      setError(errorMessage(err, 'The response could not be submitted.'))
      if (errorStatus(err) === 409) {
        await queryClient.invalidateQueries({ queryKey: reviewKeys.all(engagementId) })
      }
    } finally {
      setBusy(false)
    }
  }

  return (
    <form className="erp-response" onSubmit={onSubmit} noValidate aria-label="Respond to the auditor">
      <h3 className="erp-h3">Respond to the auditor</h3>
      {card.decision_note && (
        <div className="erp-decision">
          <span className="erp-decision-label">
            Auditor's note{card.last_decision_by ? ` · ${card.last_decision_by.name}` : ''}
          </span>
          <p className="erp-decision-text">{card.decision_note}</p>
        </div>
      )}

      <label className="erp-label" htmlFor="erp-response-note">
        Your response <span className="erp-required">(required)</span>
      </label>
      <textarea
        id="erp-response-note"
        className="erp-textarea"
        rows={3}
        aria-required="true"
        value={note}
        onChange={e => setNote(e.target.value)}
      />

      <fieldset className="erp-fieldset">
        <legend className="erp-label">Files to link</legend>
        {card.artifacts.length === 0 ? (
          <p className="erp-muted">No file is held for this evidence object yet. Upload one, or say why none is linked.</p>
        ) : (
          <ul className="erp-check-list">
            {card.artifacts.map(a => (
              <li key={a.id}>
                <label className="erp-check">
                  <input type="checkbox" checked={picked.has(a.id)} onChange={() => toggle(a.id)} />
                  <span>{a.filename}</span>
                  <span className={`erp-window ${a.in_window ? 'is-in' : 'is-out'}`}>
                    {a.in_window ? 'In window' : 'Outside window'}
                  </span>
                </label>
              </li>
            ))}
          </ul>
        )}

        <button type="button" className="erp-btn erp-btn-link" aria-expanded={showUpload} onClick={() => setShowUpload(v => !v)}>
          {showUpload ? 'Hide upload' : 'Upload a new file'}
        </button>
        {showUpload && (
          <div className="erp-upload">
            <EvidenceFileUpload orgId={organizationId} evidenceId={card.evidence_id} onUploadComplete={() => { void onUploadComplete() }} />
          </div>
        )}

        <label className="erp-check">
          <input
            type="checkbox"
            checked={noneApplies}
            onChange={e => {
              setNoneApplies(e.target.checked)
              if (e.target.checked) setPicked(new Set())
            }}
          />
          <span>Link no file</span>
        </label>
        {noneApplies && (
          <>
            <label className="erp-label" htmlFor="erp-no-artifact-reason">
              Why is no file linked? <span className="erp-required">(required)</span>
            </label>
            <textarea
              id="erp-no-artifact-reason"
              className="erp-textarea"
              rows={2}
              aria-required="true"
              value={reason}
              onChange={e => setReason(e.target.value)}
            />
          </>
        )}
      </fieldset>

      {error && <p className="erp-error" role="alert">{error}</p>}

      <div className="erp-form-actions">
        <button type="submit" className="erp-btn erp-btn-primary" disabled={busy}>
          Submit response
        </button>
      </div>
    </form>
  )
}
