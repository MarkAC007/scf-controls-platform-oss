// SPDX-License-Identifier: AGPL-3.0-only
import { parseDate } from './ReviewCardShared'
import { useCallback, useRef, useState } from 'react'
import type { AuditEngagement } from '../../data/apiClient'
import ReviewBoard from './ReviewBoard'
import EngagementDashboard from './EngagementDashboard'
import './engagement-review-tokens.css'
import './EngagementReviewWorkspace.css'

/**
 * The engagement review workspace: the shell around the review board and the
 * dashboard.
 *
 * An engagement is an auditor reviewing evidence the organisation already
 * holds, seen from a mapped framework. This shell owns what the two views
 * share — the engagement header, the board / dashboard switch and the
 * framework tabs — so the chosen framework and the header totals stay the
 * same when the reader moves between them.
 */

export type ReviewWorkspaceView = 'board' | 'dashboard'

/** What a notification or a dashboard row asks the board to open. */
export interface ReviewFocus {
  /** Evidence object id (ERL id, e.g. E-IAC-07). */
  evidenceId?: string
  /** Review row id — what a notification carries. The board resolves it to a card. */
  reviewId?: string
  /** A control with no evidence object: open its control-level query form. */
  controlScfId?: string
}

export interface ReviewTotals {
  evidence: number
  controls: number
}

interface EngagementReviewWorkspaceProps {
  organizationId: string
  engagement: AuditEngagement
  frameworkName: (id: string) => string
  onBack: () => void
  /** Text of the back link. Defaults to "Engagements". */
  backLabel?: string
  initialView?: ReviewWorkspaceView
  initialFocus?: ReviewFocus | null
  onNavigateToControl?: (scfId: string, mode?: 'in-scope' | 'full-library') => void
  onNavigateToEvidence?: (evidenceId: string) => void
}

const STATUS_LABELS: Record<string, string> = {
  draft: 'Draft',
  active: 'Active',
  under_review: 'Under review',
  closed: 'Closed',
}

const fmtDate = (d: string | null | undefined) =>
  d ? parseDate(d).toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric' }) : null

export default function EngagementReviewWorkspace({
  organizationId, engagement, frameworkName, onBack, backLabel = 'Engagements',
  initialView = 'board', initialFocus = null, onNavigateToControl, onNavigateToEvidence,
}: EngagementReviewWorkspaceProps) {
  const [view, setView] = useState<ReviewWorkspaceView>(initialView)
  const [framework, setFramework] = useState<string>(engagement.frameworks[0] ?? '')
  const [focus, setFocus] = useState<ReviewFocus | null>(initialFocus)
  // Frameworks the current focus has been looked for on. A focus carries no
  // framework, so a card missing from one tab is looked for on the next.
  const triedFrameworks = useRef<Set<string>>(new Set())
  const [totals, setTotals] = useState<ReviewTotals | null>(null)

  const handleTotals = useCallback((next: ReviewTotals) => {
    setTotals(prev => (prev && prev.evidence === next.evidence && prev.controls === next.controls ? prev : next))
  }, [])

  const openOnBoard = useCallback((next: ReviewFocus) => {
    triedFrameworks.current = new Set()
    setFocus(next)
    setView('board')
  }, [])

  const clearFocus = useCallback(() => {
    triedFrameworks.current = new Set()
    setFocus(null)
  }, [])

  /** The board could not find the focus: try the next untried framework. */
  const handleFocusMissed = useCallback((): boolean => {
    triedFrameworks.current.add(framework)
    const next = engagement.frameworks.find(fw => !triedFrameworks.current.has(fw))
    if (!next) return false
    setFramework(next)
    return true
  }, [framework, engagement.frameworks])

  const start = fmtDate(engagement.start_date)
  const end = fmtDate(engagement.end_date)
  const windowText = start && end ? `Audit window ${start} – ${end}`
    : start ? `Audit window from ${start}`
    : end ? `Audit window to ${end}`
    : 'No audit window set'

  return (
    <div className="erw">
      <header className="erw-header">
        <div className="erw-header-main">
          <button type="button" className="erw-back" onClick={onBack}>
            <span aria-hidden="true">←</span> {backLabel}
          </button>
          <div className="erw-title-row">
            <h1 className="erw-title">{engagement.name}</h1>
            <span className={`erw-status erw-status-${engagement.status}`}>
              {STATUS_LABELS[engagement.status] ?? engagement.status}
            </span>
          </div>
          <p className="erw-subline">
            {windowText}
            {totals && (
              <>
                {' · '}{totals.evidence} evidence object{totals.evidence === 1 ? '' : 's'} under test
                {' · '}{totals.controls} SCF control{totals.controls === 1 ? '' : 's'} in scope
              </>
            )}
          </p>
        </div>

        <div className="erw-view-switch" role="tablist" aria-label="Engagement view">
          <button
            type="button" role="tab" id="erw-tab-board" aria-selected={view === 'board'}
            aria-controls="erw-panel" className={`erw-view-btn${view === 'board' ? ' is-active' : ''}`}
            onClick={() => setView('board')}
          >Review board</button>
          <button
            type="button" role="tab" id="erw-tab-dashboard" aria-selected={view === 'dashboard'}
            aria-controls="erw-panel" className={`erw-view-btn${view === 'dashboard' ? ' is-active' : ''}`}
            onClick={() => setView('dashboard')}
          >Dashboard</button>
        </div>
      </header>

      {engagement.frameworks.length > 0 && (
        <nav className="erw-frameworks" aria-label="Framework">
          {engagement.frameworks.map(fw => (
            <button
              key={fw} type="button" title={fw}
              aria-pressed={fw === framework}
              className={`erw-framework-tab${fw === framework ? ' is-active' : ''}`}
              onClick={() => setFramework(fw)}
            >{frameworkName(fw)}</button>
          ))}
        </nav>
      )}

      <div
        id="erw-panel" role="tabpanel" className="erw-panel"
        aria-labelledby={view === 'board' ? 'erw-tab-board' : 'erw-tab-dashboard'}
      >
        {!framework ? (
          <p className="erw-empty">This engagement has no framework, so there is nothing to review.</p>
        ) : view === 'board' ? (
          <ReviewBoard
            organizationId={organizationId}
            engagementId={engagement.id}
            framework={framework}
            frameworkLabel={frameworkName(framework)}
            focus={focus}
            onFocusConsumed={clearFocus}
            onFocusMissed={handleFocusMissed}
            onTotals={handleTotals}
            onNavigateToControl={onNavigateToControl}
            onNavigateToEvidence={onNavigateToEvidence}
          />
        ) : (
          <EngagementDashboard
            organizationId={organizationId}
            engagementId={engagement.id}
            framework={framework}
            frameworkLabel={frameworkName(framework)}
            onOpenOnBoard={openOnBoard}
            onTotals={handleTotals}
          />
        )}
      </div>
    </div>
  )
}
