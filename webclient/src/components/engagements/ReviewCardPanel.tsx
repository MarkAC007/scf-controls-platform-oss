import { useEffect, useRef, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import {
  AI_VERDICT_LABELS,
  assignReviewer,
  getReviewArtifactLink,
  getReviewCard,
  getReviewPeople,
  listReviewEvents,
  reviewKeys,
  REVIEW_STATUS_LABELS,
  type CardArtifact,
  type EvidenceCardDetail,
  type ReviewPeople,
  type ReviewStatus,
  type ReviewViewer,
} from '../../data/engagementReviewApi'
import {
  clearAccountableTeam,
  createOrUpdateEvidenceTracking,
  listTeams,
  setAccountableTeam,
} from '../../data/apiClient'
import ReviewQueries from './ReviewQueries'
import ReviewCardStageActions from './ReviewCardStageActions'
import ReviewCardDiscussion from './ReviewCardDiscussion'
import { errorMessage, fmtDate, fmtDateTime } from './ReviewCardShared'
import './engagement-review-tokens.css'
import './ReviewCardPanel.css'

/**
 * PROPS CONTRACT written by the lead. ReviewBoard (helper e) renders the panel
 * with these props; keep this shape.
 */
export interface ReviewCardPanelProps {
  organizationId: string
  engagementId: string
  /** Framework id of the selected tab; clause chips are for this framework. */
  framework: string
  frameworkLabel: string
  /** Evidence object id (ERL id) of the card to show. */
  evidenceId: string
  /** What the caller may do, from the board response. */
  viewer: ReviewViewer
  onClose: () => void
  /** Open a control in the Control Library. Absent for an external auditor. */
  onNavigateToControl?: (scfId: string, mode?: 'in-scope' | 'full-library') => void
  /** Open the evidence object in the Evidence workspace. Absent for an external auditor. */
  onNavigateToEvidence?: (evidenceId: string) => void
  /** A move started on the board (drag or menu) that needs this panel's note form. */
  pendingMove?: ReviewStatus | null
  /** Called once the stage actions have taken `pendingMove`, so it opens only once. */
  onPendingMoveConsumed?: () => void
}

/**
 * The detail panel for one evidence object under review: what it is, where it
 * stands, the files the organisation holds for it, the in-scope controls it
 * supports, who owns and reviews it, its queries, its discussion and its
 * history. Each section loads and fails on its own, so one bad request never
 * blanks the panel.
 */
export default function ReviewCardPanel({
  organizationId, engagementId, framework, frameworkLabel, evidenceId, viewer,
  onClose, onNavigateToControl, onNavigateToEvidence, pendingMove = null, onPendingMoveConsumed,
}: ReviewCardPanelProps) {
  const queryClient = useQueryClient()
  const closeRef = useRef<HTMLButtonElement>(null)

  const card = useQuery({
    queryKey: reviewKeys.card(engagementId, evidenceId, framework),
    queryFn: () => getReviewCard(organizationId, engagementId, evidenceId, framework),
  })
  const people = useQuery({
    queryKey: reviewKeys.people(engagementId),
    queryFn: () => getReviewPeople(organizationId, engagementId),
  })

  // Escape closes the panel, unless an inline form inside it handled the key.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && !e.defaultPrevented) onClose()
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [onClose])

  // Opening another card moves focus into the panel, so a keyboard user lands on it.
  useEffect(() => { closeRef.current?.focus() }, [evidenceId])

  const data = card.data
  const invalidateAll = () => queryClient.invalidateQueries({ queryKey: reviewKeys.all(engagementId) })

  return (
    // The board's slot is the <aside> landmark and owns size, scroll and border.
    <div className="erp" role="region" aria-label={`Evidence object ${evidenceId} details`}>
      <header className="erp-header">
        <div className="erp-header-row">
          <span className="erp-id">{evidenceId}</span>
          {data && (
            <span className={`erp-stage erp-stage-${data.status}`}>{REVIEW_STATUS_LABELS[data.status]}</span>
          )}
          <button ref={closeRef} type="button" className="erp-close" aria-label="Close panel" onClick={onClose}>
            <span aria-hidden="true">×</span>
          </button>
        </div>
        {card.isPending && <p className="erp-muted">Loading evidence object…</p>}
        {card.isError && (
          <p className="erp-error" role="alert">{errorMessage(card.error, 'This evidence object could not be loaded.')}</p>
        )}
        {data && (
          <>
            <h2 className="erp-title">{data.title ?? evidenceId}</h2>
            <p className="erp-subline">
              Reviewer {data.reviewer?.name ?? 'unassigned'} ·{' '}
              <span className={`erp-verdict erp-verdict-${data.ai_verdict}`}>{AI_VERDICT_LABELS[data.ai_verdict]}</span>
            </p>
            {data.description && <p className="erp-description">{data.description}</p>}
            {onNavigateToEvidence && (
              <button type="button" className="erp-btn-link" onClick={() => onNavigateToEvidence(data.evidence_id)}>
                Open in the Evidence workspace
              </button>
            )}
          </>
        )}
      </header>

      <div className="erp-body">
        {data && (
          <>
            <ReviewCardStageActions
              organizationId={organizationId}
              engagementId={engagementId}
              framework={framework}
              card={data}
              viewer={viewer}
              pendingMove={pendingMove}
              onPendingMoveConsumed={onPendingMoveConsumed}
            />
            <ArtifactsSection
              organizationId={organizationId}
              engagementId={engagementId}
              card={data}
            />
            <ControlsSection
              card={data}
              frameworkLabel={frameworkLabel}
              onNavigateToControl={onNavigateToControl}
            />
            <PeopleSection
              organizationId={organizationId}
              engagementId={engagementId}
              framework={framework}
              card={data}
              viewer={viewer}
              people={people.data}
              peopleError={people.isError ? errorMessage(people.error, 'People could not be loaded.') : null}
              onChanged={invalidateAll}
            />
          </>
        )}

        <section className="erp-section" aria-labelledby="erp-queries-h">
          <h3 className="erp-h3" id="erp-queries-h">Queries</h3>
          <ReviewQueries
            organizationId={organizationId}
            engagementId={engagementId}
            target={{ evidenceId }}
            viewer={viewer}
          />
        </section>

        <ReviewCardDiscussion
          organizationId={organizationId}
          engagementId={engagementId}
          evidenceId={evidenceId}
          people={people.data}
        />

        <EventsSection organizationId={organizationId} engagementId={engagementId} evidenceId={evidenceId} />
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Artifacts
// ---------------------------------------------------------------------------

const WINDOW_BASIS: Record<string, string> = {
  asserted_period: 'by its stated effective period',
  upload_date: 'by its upload date',
}

function ArtifactsSection({ organizationId, engagementId, card }: {
  organizationId: string; engagementId: string; card: EvidenceCardDetail
}) {
  const [openingId, setOpeningId] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  // The tab opens inside the click so a popup blocker lets it through; the
  // signed link is fetched afterwards and the tab sent to it.
  const open = async (a: CardArtifact) => {
    setError(null)
    setOpeningId(a.id)
    const tab = window.open('about:blank', '_blank')
    if (tab) tab.opener = null
    try {
      const link = await getReviewArtifactLink(organizationId, engagementId, card.evidence_id, a.id)
      if (!link.download_url) {
        tab?.close()
        setError(`No download link is available for ${a.filename}.`)
        return
      }
      const url = new URL(link.download_url, window.location.origin).href
      if (tab) tab.location.href = url
      else window.open(url, '_blank', 'noopener,noreferrer')
    } catch (err) {
      tab?.close()
      setError(`Could not open ${a.filename}: ${errorMessage(err, 'unknown error')}`)
    } finally {
      setOpeningId(null)
    }
  }

  return (
    <section className="erp-section" aria-labelledby="erp-artifacts-h">
      <h3 className="erp-h3" id="erp-artifacts-h">
        Artifacts under test · {card.file_count} file{card.file_count === 1 ? '' : 's'}, {card.in_window_count} in window
      </h3>
      {card.artifacts.length === 0 ? (
        <p className="erp-empty">The organisation holds no artifact for this evidence object.</p>
      ) : (
        <ul className="erp-artifacts">
          {card.artifacts.map(a => {
            const start = fmtDate(a.effective_start)
            const end = fmtDate(a.effective_end)
            const period = start && end ? `${start} – ${end}` : start ? `from ${start}` : end ? `to ${end}` : null
            return (
              <li key={a.id} className="erp-artifact">
                <div className="erp-artifact-row">
                  <button
                    type="button"
                    className="erp-artifact-name"
                    onClick={() => void open(a)}
                    aria-busy={openingId === a.id}
                    title={`Open ${a.filename}`}
                  >
                    {a.filename}
                  </button>
                  <span className={`erp-window ${a.in_window ? 'is-in' : 'is-out'}`}>
                    {a.in_window ? 'In window' : 'Outside window'}
                  </span>
                </div>
                <p className="erp-artifact-meta">
                  {a.uploaded_at ? `Uploaded ${fmtDate(a.uploaded_at)}` : 'Upload date not recorded'}
                  {' · '}{period ? `Effective ${period}` : 'No effective period stated'}
                  {' · '}Window judged {WINDOW_BASIS[a.window_basis] ?? a.window_basis}
                  {a.ai_status && <>{' · '}<span className="erp-ai-status">AI: {a.ai_status}</span></>}
                </p>
              </li>
            )
          })}
        </ul>
      )}
      {error && <p className="erp-error" role="alert">{error}</p>}
    </section>
  )
}

// ---------------------------------------------------------------------------
// Supported controls
// ---------------------------------------------------------------------------

function ControlsSection({ card, frameworkLabel, onNavigateToControl }: {
  card: EvidenceCardDetail
  frameworkLabel: string
  onNavigateToControl?: ReviewCardPanelProps['onNavigateToControl']
}) {
  return (
    <section className="erp-section" aria-labelledby="erp-controls-h">
      <h3 className="erp-h3" id="erp-controls-h">Supports these in-scope controls</h3>
      {card.controls.length === 0 ? (
        <p className="erp-empty">This evidence object maps to no in-scope control in {frameworkLabel}.</p>
      ) : (
        <ul className="erp-controls">
          {card.controls.map(c => (
            <li key={c.scf_id} className="erp-control">
              <div className="erp-control-row">
                {onNavigateToControl ? (
                  <button
                    type="button"
                    className="erp-control-id is-link"
                    title={`Open ${c.scf_id} in the Control Library`}
                    onClick={() => onNavigateToControl(c.scf_id, 'in-scope')}
                  >{c.scf_id}</button>
                ) : (
                  <span className="erp-control-id">{c.scf_id}</span>
                )}
                {c.title && <span className="erp-control-title">{c.title}</span>}
              </div>
              {c.clauses.length > 0 && (
                <div className="erp-clauses">
                  <span className="erp-clauses-label">{frameworkLabel}</span>
                  {c.clauses.map(cl => <span key={cl} className="erp-clause">{cl}</span>)}
                </div>
              )}
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}

// ---------------------------------------------------------------------------
// Owner and reviewer
// ---------------------------------------------------------------------------


function PeopleSection({
  organizationId, engagementId, framework, card, viewer, people, peopleError, onChanged,
}: {
  organizationId: string
  engagementId: string
  framework: string
  card: EvidenceCardDetail
  viewer: ReviewViewer
  people: ReviewPeople | undefined
  peopleError: string | null
  onChanged: () => Promise<unknown>
}) {
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const changeReviewer = async (value: string) => {
    setBusy(true); setError(null)
    try {
      await assignReviewer(organizationId, engagementId, card.evidence_id, framework, value || null)
      await onChanged()
    } catch (err) {
      setError(errorMessage(err, 'The reviewer could not be changed.'))
    } finally { setBusy(false) }
  }

  // Evidence is owned by a team, never assigned to a person: the owner control
  // sets the accountable team through the org's team-assignment route (admin
  // only), the same one the evidence screens use. Never offered to an auditor.
  const canPickOwner = viewer.can_reassign_owner && viewer.org_role !== null
  const teams = useQuery({
    queryKey: ['teams', organizationId],
    queryFn: () => listTeams(organizationId),
    enabled: canPickOwner,
  })

  const changeTeam = async (teamId: string) => {
    setBusy(true); setError(null)
    try {
      if (teamId) {
        // A team hangs off the evidence tracking row; an untracked evidence id
        // gets one first. Only evidence_id is sent, so nothing else changes.
        const trackingId = card.owner.tracking_id
          ?? (await createOrUpdateEvidenceTracking({ evidence_id: card.evidence_id }, organizationId)).id
        await setAccountableTeam(organizationId, { type: 'evidence', itemId: trackingId, teamId })
      } else if (card.owner.team_id && card.owner.tracking_id) {
        await clearAccountableTeam(organizationId, {
          type: 'evidence', itemId: card.owner.tracking_id, teamId: card.owner.team_id,
        })
      }
      await onChanged()
    } catch (err) {
      setError(errorMessage(err, 'The accountable team could not be changed.'))
    } finally { setBusy(false) }
  }

  // Read-and-clear only: a row stamped with a person before evidence became
  // team-owned can be handed back to its team.
  const clearPerson = async () => {
    setBusy(true); setError(null)
    try {
      await createOrUpdateEvidenceTracking(
        { evidence_id: card.evidence_id, assigned_user_id: null }, organizationId,
      )
      await onChanged()
    } catch (err) {
      setError(errorMessage(err, 'The person could not be cleared.'))
    } finally { setBusy(false) }
  }

  const canPickReviewer = viewer.can_assign_reviewer && people
  const isUnowned = card.owner.tier === 'org_admin' || card.owner.tier === null

  return (
    <section className="erp-section" aria-labelledby="erp-people-h">
      <h3 className="erp-h3" id="erp-people-h">Owner and reviewer</h3>
      <dl className="erp-people">
        <dt>Reviewer</dt>
        <dd>
          {canPickReviewer ? (
            <>
              <label className="erp-sr-only" htmlFor="erp-reviewer-select">Reviewer</label>
              <select
                id="erp-reviewer-select"
                className="erp-select"
                value={card.reviewer?.user_id ?? ''}
                onChange={e => void changeReviewer(e.target.value)}
                disabled={busy}
              >
                <option value="">Unassigned</option>
                {people.auditors.map(a => (
                  <option key={a.user_id} value={a.user_id}>
                    {a.name}{a.kind === 'internal' ? ' (internal auditor)' : ''}
                  </option>
                ))}
              </select>
            </>
          ) : (
            <span>{card.reviewer?.name ?? 'Unassigned'}</span>
          )}
        </dd>

        <dt>Owner</dt>
        <dd>
          <span className={isUnowned ? 'erp-unowned' : undefined}>{card.owner.label}</span>
          {card.owner.team_name && card.owner.team_name !== card.owner.label && (
            <span className="erp-team"> · Team {card.owner.team_name}</span>
          )}
          {canPickOwner && (
            <div className="erp-owner-pick">
              <label className="erp-label" htmlFor="erp-owner-select">Accountable team</label>
              <select
                id="erp-owner-select"
                className="erp-select"
                value={card.owner.team_id ?? ''}
                onChange={e => void changeTeam(e.target.value)}
                disabled={busy || !teams.data}
              >
                <option value="">No accountable team (falls back to org admins)</option>
                {(teams.data ?? []).map(t => (
                  <option key={t.id} value={t.id}>{t.name}</option>
                ))}
              </select>
              {card.owner.assigned_user_id && (
                <button type="button" className="erp-btn-link" onClick={() => void clearPerson()} disabled={busy}>
                  Clear {card.owner.label} and hand back to the team
                </button>
              )}
            </div>
          )}
        </dd>
      </dl>
      {peopleError && <p className="erp-error" role="alert">{peopleError}</p>}
      {error && <p className="erp-error" role="alert">{error}</p>}
    </section>
  )
}

// ---------------------------------------------------------------------------
// Event history
// ---------------------------------------------------------------------------

function EventsSection({ organizationId, engagementId, evidenceId }: {
  organizationId: string; engagementId: string; evidenceId: string
}) {
  const events = useQuery({
    queryKey: reviewKeys.events(engagementId, evidenceId),
    queryFn: () => listReviewEvents(organizationId, engagementId, evidenceId),
  })

  return (
    <section className="erp-section" aria-labelledby="erp-events-h">
      <h3 className="erp-h3" id="erp-events-h">History</h3>
      {events.isPending && <p className="erp-muted">Loading history…</p>}
      {events.isError && (
        <p className="erp-error" role="alert">{errorMessage(events.error, 'History could not be loaded.')}</p>
      )}
      {events.data && events.data.length === 0 && <p className="erp-muted">No changes recorded yet.</p>}
      {events.data && events.data.length > 0 && (
        <ol className="erp-events">
          {events.data.map(ev => (
            <li key={ev.id} className="erp-event">
              <div className="erp-event-head">
                <span className="erp-event-who">{ev.actor?.name ?? 'System'}</span>
                <span className={`erp-actor erp-actor-${ev.actor_kind}`}>
                  {ev.actor_kind === 'auditor' ? 'Auditor' : 'Organisation'}
                </span>
                {ev.created_at && <span className="erp-comment-when">{fmtDateTime(ev.created_at)}</span>}
              </div>
              <p className="erp-event-what">
                {ev.event_type === 'reviewer_assigned' ? (
                  'Reviewer assigned'
                ) : (
                  <>
                    {REVIEW_STATUS_LABELS[ev.from_status]} <span aria-hidden="true">→</span>
                    <span className="erp-sr-only"> to </span> {REVIEW_STATUS_LABELS[ev.to_status]}
                  </>
                )}
              </p>
              {ev.note && <p className="erp-event-note">{ev.note}</p>}
            </li>
          ))}
        </ol>
      )}
    </section>
  )
}
