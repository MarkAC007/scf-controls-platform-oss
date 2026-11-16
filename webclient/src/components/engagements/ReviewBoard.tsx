import { useCallback, useEffect, useMemo, useRef, useState, type DragEvent, type KeyboardEvent } from 'react'
import { keepPreviousData, useMutation, useQuery, useQueryClient, type QueryKey } from '@tanstack/react-query'
import type { ReviewFocus, ReviewTotals } from './EngagementReviewWorkspace'
import {
  AI_VERDICT_LABELS,
  REVIEW_STATUSES,
  REVIEW_STATUS_HINTS,
  REVIEW_STATUS_LABELS,
  changeReviewStatus,
  getReviewBoard,
  reviewKeys,
  type AiVerdict,
  type EvidenceCard,
  type EvidenceReviewBoard,
  type NoEvidenceControl,
  type ReviewBoardFilters,
  type ReviewStatus,
} from '../../data/engagementReviewApi'
import ReviewBoardCard from './ReviewBoardCard'
import ReviewBoardControlDialog from './ReviewBoardControlDialog'
import ReviewCardPanel from './ReviewCardPanel'
import './ReviewBoard.css'

/**
 * PROPS CONTRACT written by the lead. Helper (e) replaces the body of this
 * file with the real board; the exported props must keep this shape because
 * EngagementReviewWorkspace renders the board with them.
 */
export interface ReviewBoardProps {
  organizationId: string
  engagementId: string
  framework: string
  frameworkLabel: string
  /** A card or control to open (from a notification or the dashboard). */
  focus: ReviewFocus | null
  /** Call once the focus has been acted on, so it does not reopen. */
  onFocusConsumed: () => void
  /**
   * The focus is not on this tab. Return true when the caller will look for it
   * elsewhere (another framework tab): the board then shows no notice and does
   * not consume the focus.
   */
  onFocusMissed?: () => boolean
  /** Report the header totals (evidence under test, controls in scope). */
  onTotals: (totals: ReviewTotals) => void
  onNavigateToControl?: (scfId: string, mode?: 'in-scope' | 'full-library') => void
  onNavigateToEvidence?: (evidenceId: string) => void
}

const SEARCH_DEBOUNCE_MS = 300

/** Filters chosen in the bar, except search (debounced separately). */
type BarFilters = Omit<ReviewBoardFilters, 'search' | 'status'>

type MoveError = { kind: 'conflict' | 'forbidden' | 'other'; message: string }

interface MoveVars {
  card: EvidenceCard
  to: ReviewStatus
  key: QueryKey
}

/** The board as it would look with one card moved; used for the optimistic update. */
export function moveCardInBoard(board: EvidenceReviewBoard, evidenceId: string, to: ReviewStatus): EvidenceReviewBoard {
  const current = board.cards.find(c => c.evidence_id === evidenceId)
  if (!current || current.status === to) return board
  const counts = { ...board.counts }
  counts[current.status] = Math.max(0, counts[current.status] - 1)
  counts[to] = counts[to] + 1
  return {
    ...board,
    counts,
    cards: board.cards.map(c =>
      c.evidence_id === evidenceId
        // No further moves until the server answers with the card's new rules.
        ? { ...c, status: to, allowed_transitions: [], note_required: {} }
        : c),
  }
}

function errorStatus(err: unknown): number | undefined {
  const s = (err as { status?: unknown } | null)?.status
  return typeof s === 'number' ? s : undefined
}

function errorMessage(err: unknown): string {
  return err instanceof Error && err.message ? err.message : 'The request failed.'
}

/** Remount per framework tab so filters, selection and dialogs start clean. */
export default function ReviewBoard(props: ReviewBoardProps) {
  return <ReviewBoardInner key={props.framework} {...props} />
}

function ReviewBoardInner({
  organizationId, engagementId, framework, frameworkLabel, focus, onFocusConsumed, onFocusMissed, onTotals,
  onNavigateToControl, onNavigateToEvidence,
}: ReviewBoardProps) {
  const queryClient = useQueryClient()

  // ---- filters -----------------------------------------------------------
  const [bar, setBar] = useState<BarFilters>({})
  const [searchInput, setSearchInput] = useState('')
  const [search, setSearch] = useState('')

  useEffect(() => {
    if (searchInput === search) return
    const t = setTimeout(() => setSearch(searchInput), SEARCH_DEBOUNCE_MS)
    return () => clearTimeout(t)
  }, [searchInput, search])

  const filters = useMemo<ReviewBoardFilters>(() => {
    const f: ReviewBoardFilters = {}
    if (bar.clause) f.clause = bar.clause
    if (bar.scfId) f.scfId = bar.scfId
    if (bar.aiVerdict) f.aiVerdict = bar.aiVerdict
    if (bar.hasOpenQueries) f.hasOpenQueries = true
    if (bar.reviewer) f.reviewer = bar.reviewer
    if (bar.mine) f.mine = true
    if (search.trim()) f.search = search.trim()
    return f
  }, [bar, search])

  const filtersApplied = Object.keys(filters).length > 0
  const filtersShown = filtersApplied || searchInput.trim() !== ''

  const clearFilters = useCallback(() => {
    setBar({})
    setSearchInput('')
    setSearch('')
  }, [])

  const setBarValue = <K extends keyof BarFilters>(key: K, value: BarFilters[K] | undefined) => {
    setBar(prev => {
      const next = { ...prev }
      if (value === undefined || value === '' || value === false) delete next[key]
      else next[key] = value
      return next
    })
  }

  // ---- data --------------------------------------------------------------
  const boardKey = reviewKeys.board(engagementId, framework, filters)
  const boardQuery = useQuery({
    queryKey: boardKey,
    queryFn: () => getReviewBoard(organizationId, engagementId, framework, filters),
    placeholderData: keepPreviousData,
  })
  const board = boardQuery.data

  useEffect(() => {
    if (board) onTotals(board.totals)
  }, [board, onTotals])

  // ---- selection, dialogs, notices --------------------------------------
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [control, setControl] = useState<NoEvidenceControl | null>(null)
  const [moveError, setMoveError] = useState<MoveError | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  // A drag or menu move that needs a note: the panel opens with its form ready.
  const [pendingMove, setPendingMove] = useState<{ evidenceId: string; to: ReviewStatus } | null>(null)
  const [pending, setPending] = useState<Set<string>>(() => new Set())
  const openerRef = useRef<HTMLElement | null>(null)
  const boardRef = useRef<HTMLDivElement>(null)

  const openCard = useCallback((evidenceId: string, opener?: HTMLElement | null) => {
    if (opener) openerRef.current = opener
    setPendingMove(prev => (prev && prev.evidenceId !== evidenceId ? null : prev))
    setSelectedId(evidenceId)
  }, [])

  const closePanel = useCallback(() => {
    const id = selectedId
    setSelectedId(null)
    setPendingMove(null)
    // Focus returns to the card that opened the panel.
    requestAnimationFrame(() => {
      const opener = openerRef.current
      if (opener && opener.isConnected) { opener.focus(); return }
      const cardEl = Array.from(boardRef.current?.querySelectorAll<HTMLElement>('[data-evidence-id]') ?? [])
        .find(el => el.dataset.evidenceId === id)
      cardEl?.querySelector<HTMLElement>('.erb-card-open')?.focus()
    })
  }, [selectedId])

  const clearPendingMove = useCallback(() => setPendingMove(null), [])

  const invalidateAll = useCallback(
    () => queryClient.invalidateQueries({ queryKey: reviewKeys.all(engagementId) }),
    [queryClient, engagementId],
  )

  // ---- moving a card ----------------------------------------------------
  const move = useMutation({
    mutationFn: ({ card, to }: MoveVars) =>
      changeReviewStatus(organizationId, engagementId, card.evidence_id, framework, {
        to_status: to,
        from_status: card.status,
      }),
    onMutate: async ({ card, to, key }) => {
      setMoveError(null)
      setPending(prev => new Set(prev).add(card.evidence_id))
      await queryClient.cancelQueries({ queryKey: key })
      const previous = queryClient.getQueryData<EvidenceReviewBoard>(key)
      if (previous) queryClient.setQueryData(key, moveCardInBoard(previous, card.evidence_id, to))
      return { previous, key }
    },
    onError: (err, { card, to }, ctx) => {
      if (ctx?.previous) queryClient.setQueryData(ctx.key, ctx.previous)
      const status = errorStatus(err)
      const msg = errorMessage(err)
      const what = `${card.evidence_id} was not moved to ${REVIEW_STATUS_LABELS[to]}.`
      if (status === 409) {
        setMoveError({ kind: 'conflict', message: `${what} Someone else has already moved it: ${msg} The board has been refreshed.` })
      } else if (status === 403) {
        setMoveError({ kind: 'forbidden', message: `${what} You are not permitted to make this move: ${msg}` })
      } else {
        setMoveError({ kind: 'other', message: `${what} ${msg}` })
      }
      // The snapshot restored above may predate another move still in flight,
      // so the board is always fetched again after a failed move.
      void invalidateAll()
    },
    onSuccess: () => { void invalidateAll() },
    onSettled: (_d, _e, { card }) => {
      setPending(prev => {
        const next = new Set(prev)
        next.delete(card.evidence_id)
        return next
      })
    },
  })

  const requestMove = useCallback((card: EvidenceCard, to: ReviewStatus) => {
    if (card.status === to || !card.allowed_transitions.includes(to)) return
    if (card.note_required[to] || to === 'submitted') {
      // The panel carries the form for this stage; the move is completed there.
      setNotice(`Complete the move of ${card.evidence_id} to ${REVIEW_STATUS_LABELS[to]} in the panel: it needs a note.`)
      openCard(card.evidence_id)
      setPendingMove({ evidenceId: card.evidence_id, to })
      return
    }
    move.mutate({ card, to, key: boardKey })
  }, [move, boardKey, openCard])

  // ---- drag and drop ----------------------------------------------------
  const [dragging, setDragging] = useState<EvidenceCard | null>(null)
  const [overColumn, setOverColumn] = useState<ReviewStatus | null>(null)

  const accepts = (status: ReviewStatus) => !!dragging && dragging.allowed_transitions.includes(status)

  const onColumnDragOver = (status: ReviewStatus) => (e: DragEvent<HTMLElement>) => {
    if (!accepts(status)) return // not preventing default refuses the drop
    e.preventDefault()
    e.dataTransfer.dropEffect = 'move'
    if (overColumn !== status) setOverColumn(status)
  }

  const onColumnDrop = (status: ReviewStatus) => (e: DragEvent<HTMLElement>) => {
    const card = dragging
    setDragging(null)
    setOverColumn(null)
    if (!card || !card.allowed_transitions.includes(status)) return
    e.preventDefault()
    requestMove(card, status)
  }

  // ---- focus from a notification or the dashboard -----------------------
  const focusRetried = useRef(false)
  useEffect(() => {
    if (!focus || !board || boardQuery.isPlaceholderData || boardQuery.isFetching) return
    const finish = () => {
      focusRetried.current = false
      onFocusConsumed()
    }
    const retry = () => {
      if (filtersApplied && !focusRetried.current) {
        focusRetried.current = true
        clearFilters()
        return true
      }
      return false
    }

    if (focus.controlScfId) {
      const ctl = board.no_evidence_controls.find(c => c.scf_id === focus.controlScfId)
      if (ctl) { setControl(ctl); finish(); return }
      if (retry()) return
      if (onFocusMissed?.()) return
      setNotice(`${focus.controlScfId} is not a control without evidence on the ${frameworkLabel} tab.`)
      finish()
      return
    }

    const card = focus.evidenceId
      ? board.cards.find(c => c.evidence_id === focus.evidenceId)
      : focus.reviewId
        ? board.cards.find(c => c.review_id === focus.reviewId)
        : undefined
    if (card) { openCard(card.evidence_id); finish(); return }
    if (!focus.evidenceId && !focus.reviewId) { finish(); return }
    if (retry()) return
    if (onFocusMissed?.()) return
    setNotice(focus.evidenceId
      ? `${focus.evidenceId} is not on the ${frameworkLabel} tab.`
      : `That evidence review is not on the ${frameworkLabel} tab.`)
    finish()
  }, [focus, board, boardQuery.isPlaceholderData, boardQuery.isFetching, filtersApplied, clearFilters,
    frameworkLabel, onFocusConsumed, onFocusMissed, openCard])

  // ---- rendering --------------------------------------------------------
  if (!board) {
    if (boardQuery.isError) {
      return (
        <div className="erb-state erb-state-error" role="alert">
          <p>The {frameworkLabel} review board could not be loaded: {errorMessage(boardQuery.error)}</p>
          <button type="button" className="erb-btn" onClick={() => void boardQuery.refetch()}>Retry</button>
        </div>
      )
    }
    return (
      <div className="erb-state" role="status">
        <p>Loading the {frameworkLabel} review board: evidence objects, their stages and open queries…</p>
      </div>
    )
  }

  const selectedCard = selectedId ? board.cards.find(c => c.evidence_id === selectedId) ?? null : null
  const totalCards = board.cards.length
  const isEmpty = board.totals.evidence === 0 && board.no_evidence_controls.length === 0 && !filtersApplied

  const onPanelKeyDown = (e: KeyboardEvent<HTMLElement>) => {
    if (e.key === 'Escape' && !e.defaultPrevented) { e.preventDefault(); closePanel() }
  }

  return (
    <div className="erb" ref={boardRef}>
      <div className="erb-filters" role="search" aria-label="Filter evidence objects">
        <label className="erb-search">
          <svg className="erb-icon" viewBox="0 0 24 24" aria-hidden="true">
            <circle cx="11" cy="11" r="7" /><path d="M20 20l-3.5-3.5" />
          </svg>
          <span className="erb-visually-hidden">Search evidence, control or clause</span>
          <input
            type="search"
            placeholder="Search evidence, control or clause"
            value={searchInput}
            onChange={e => setSearchInput(e.target.value)}
          />
        </label>
        <label className="erb-field">Clause
          <select value={bar.clause ?? ''} onChange={e => setBarValue('clause', e.target.value)}>
            <option value="">All clauses</option>
            {board.filter_options.clauses.map(c => <option key={c} value={c}>{c}</option>)}
          </select>
        </label>
        <label className="erb-field">SCF control
          <select value={bar.scfId ?? ''} onChange={e => setBarValue('scfId', e.target.value)}>
            <option value="">All controls</option>
            {board.filter_options.scf_ids.map(c => <option key={c} value={c}>{c}</option>)}
          </select>
        </label>
        <label className="erb-field">AI verdict
          <select
            value={bar.aiVerdict ?? ''}
            onChange={e => setBarValue('aiVerdict', (e.target.value || undefined) as AiVerdict | undefined)}
          >
            <option value="">Any</option>
            {(Object.keys(AI_VERDICT_LABELS) as AiVerdict[]).map(v => (
              <option key={v} value={v}>{AI_VERDICT_LABELS[v].replace(/^AI: /, '')}</option>
            ))}
          </select>
        </label>
        <label className="erb-field">Reviewer
          <select value={bar.reviewer ?? ''} onChange={e => setBarValue('reviewer', e.target.value)}>
            <option value="">Anyone</option>
            <option value="none">Unassigned</option>
            {board.filter_options.reviewers.map(r => <option key={r.user_id} value={r.user_id}>{r.name}</option>)}
          </select>
        </label>
        <label className="erb-check">
          <input
            type="checkbox"
            checked={!!bar.hasOpenQueries}
            onChange={e => setBarValue('hasOpenQueries', e.target.checked)}
          />
          Has open queries
        </label>
        <button
          type="button"
          className={`erb-toggle${bar.mine ? ' is-on' : ''}`}
          aria-pressed={!!bar.mine}
          title={board.viewer.is_auditor ? 'Cards you review' : 'Cards you review or own'}
          onClick={() => setBarValue('mine', !bar.mine)}
        >My cards</button>
        {filtersShown && (
          <button type="button" className="erb-link-btn" onClick={clearFilters}>Clear filters</button>
        )}
        <span className="erb-match" role="status" aria-live="polite">
          {filtersApplied
            ? `${totalCards} of ${board.totals.evidence} evidence object${board.totals.evidence === 1 ? '' : 's'} match`
            : `${totalCards} evidence object${totalCards === 1 ? '' : 's'}`}
          {boardQuery.isFetching && ' · updating…'}
        </span>
      </div>

      {moveError && (
        <div className={`erb-banner erb-banner-${moveError.kind}`} role="alert">
          <span>{moveError.message}</span>
          <button type="button" className="erb-icon-btn" aria-label="Dismiss" onClick={() => setMoveError(null)}>×</button>
        </div>
      )}
      {notice && (
        <div className="erb-banner erb-banner-info" role="status">
          <span>{notice}</span>
          <button type="button" className="erb-icon-btn" aria-label="Dismiss" onClick={() => setNotice(null)}>×</button>
        </div>
      )}
      {boardQuery.isError && (
        <div className="erb-banner erb-banner-other" role="alert">
          <span>The board could not be refreshed: {errorMessage(boardQuery.error)}</span>
          <button type="button" className="erb-btn" onClick={() => void boardQuery.refetch()}>Retry</button>
        </div>
      )}

      {board.no_evidence_controls.length > 0 && (
        <section className="erb-strip" aria-labelledby="erb-strip-title">
          <div className="erb-strip-head">
            <h2 id="erb-strip-title" className="erb-strip-title">No evidence — resolve by query</h2>
            <span className="erb-strip-hint">
              {board.no_evidence_controls.length} in-scope control{board.no_evidence_controls.length === 1 ? '' : 's'} with
              no evidence object. They stay Open until a control-level query resolves them.
            </span>
          </div>
          <ul className="erb-strip-list">
            {board.no_evidence_controls.map(c => (
              <li key={c.scf_id}>
                <button
                  type="button"
                  className="erb-strip-item"
                  aria-haspopup="dialog"
                  onClick={() => setControl(c)}
                >
                  <span className="erb-strip-id">{c.scf_id}</span>
                  {c.title && <span className="erb-strip-name">{c.title}</span>}
                  {c.clauses.length > 0 && (
                    <span className="erb-chip-row">
                      {c.clauses.slice(0, 4).map(cl => <span key={cl} className="erb-chip erb-chip-clause">{cl}</span>)}
                      {c.clauses.length > 4 && (
                        <span className="erb-chip erb-chip-clause erb-chip-more" title={c.clauses.slice(4).join(', ')}>
                          +{c.clauses.length - 4}
                        </span>
                      )}
                    </span>
                  )}
                  <span className={`erb-strip-queries${c.open_query_count > 0 ? ' has-open' : ''}`}>
                    {c.open_query_count} open quer{c.open_query_count === 1 ? 'y' : 'ies'}
                  </span>
                </button>
              </li>
            ))}
          </ul>
        </section>
      )}

      {isEmpty ? (
        <div className="erb-state">
          <p>No evidence object maps to an in-scope control for {frameworkLabel}, so there is nothing to review on this tab.</p>
        </div>
      ) : (
        <div className={`erb-layout${selectedId ? ' has-panel' : ''}`}>
          <div className="erb-columns-scroll">
            <div className={`erb-columns${dragging ? ' is-dragging' : ''}`}>
              {REVIEW_STATUSES.map(status => {
                const cards = board.cards.filter(c => c.status === status)
                const target = dragging ? accepts(status) : false
                return (
                  <section
                    key={status}
                    aria-label={REVIEW_STATUS_LABELS[status]}
                    data-status={status}
                    className={`erb-column erb-stage-${status}${dragging && target ? ' is-drop-target' : ''}${dragging && !target ? ' is-drop-refused' : ''}${overColumn === status ? ' is-drop-over' : ''}`}
                    onDragOver={onColumnDragOver(status)}
                    onDragLeave={() => { if (overColumn === status) setOverColumn(null) }}
                    onDrop={onColumnDrop(status)}
                  >
                    <header className="erb-column-head">
                      <h3 className="erb-column-title">{REVIEW_STATUS_LABELS[status]}</h3>
                      <span className="erb-column-count" aria-label={`${board.counts[status]} cards`}>
                        {board.counts[status]}
                      </span>
                    </header>
                    <p className="erb-column-hint">{REVIEW_STATUS_HINTS[status]}</p>
                    <div className="erb-column-body">
                      {cards.length === 0 ? (
                        <p className="erb-column-empty">
                          {filtersApplied ? 'No matching evidence objects in this stage.' : 'No evidence objects in this stage.'}
                        </p>
                      ) : cards.map(card => (
                        <ReviewBoardCard
                          key={card.evidence_id}
                          card={card}
                          frameworkLabel={frameworkLabel}
                          selected={card.evidence_id === selectedId}
                          pending={pending.has(card.evidence_id)}
                          onOpen={(c, opener) => openCard(c.evidence_id, opener)}
                          onMove={requestMove}
                          onDragStart={setDragging}
                          onDragEnd={() => { setDragging(null); setOverColumn(null) }}
                        />
                      ))}
                    </div>
                  </section>
                )
              })}
            </div>
          </div>

          {selectedId && (
            <aside
              className="erb-panel"
              aria-label={`Evidence object ${selectedId}${selectedCard?.title ? `, ${selectedCard.title}` : ''}`}
              onKeyDown={onPanelKeyDown}
            >
              <ReviewCardPanel
                organizationId={organizationId}
                engagementId={engagementId}
                framework={framework}
                frameworkLabel={frameworkLabel}
                evidenceId={selectedId}
                viewer={board.viewer}
                onClose={closePanel}
                pendingMove={pendingMove?.evidenceId === selectedId ? pendingMove.to : null}
                onPendingMoveConsumed={clearPendingMove}
                onNavigateToControl={onNavigateToControl}
                onNavigateToEvidence={onNavigateToEvidence}
              />
            </aside>
          )}
        </div>
      )}

      {control && (
        <ReviewBoardControlDialog
          organizationId={organizationId}
          engagementId={engagementId}
          frameworkLabel={frameworkLabel}
          control={control}
          viewer={board.viewer}
          onClose={() => setControl(null)}
          onChanged={() => { void invalidateAll() }}
          onNavigateToControl={onNavigateToControl}
        />
      )}
    </div>
  )
}
