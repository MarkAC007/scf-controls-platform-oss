// SPDX-License-Identifier: AGPL-3.0-only
import { useCallback, useEffect, useMemo, useState } from 'react'
import { listMyEngagements, type AuditEngagement } from '../../data/apiClient'
import { fetchFrameworks, type FrameworkInfo } from '../../data/catalogApi'
import { useAuth } from '../../contexts/AuthContext'
import { getAppTitle } from '../../branding'
import { NotificationBell, type EngagementReviewTarget } from '../NotificationBell'
import ThemeMenu from '../ThemeMenu'
import NoOrganisationScreen from '../NoOrganisationScreen'
import EngagementReviewWorkspace, { type ReviewFocus } from './EngagementReviewWorkspace'
import { parseDate } from './ReviewCardShared'
import './engagement-review-tokens.css'
import './AuditorWorkspace.css'

/**
 * What a signed-in person sees when they belong to no organisation but hold
 * an auditor grant: the engagements they audit, and nothing else.
 *
 * An external auditor is not a member of the organisation they audit, so the
 * rest of the app — which is built around an organisation — has nothing to
 * show them. This is their whole workspace: a list of their engagements, the
 * review board and dashboard for each, the notification bell, and sign out.
 * Someone with no grant either falls through to the "no organisation" screen.
 */

const STATUS_LABELS: Record<string, string> = {
  draft: 'Draft', active: 'Active', under_review: 'Under review', closed: 'Closed',
}

const fmtDate = (d: string | null) =>
  d ? parseDate(d).toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric' }) : null

interface OpenEngagement {
  engagement: AuditEngagement
  focus: ReviewFocus | null
}

export default function AuditorWorkspace() {
  const { user, logout } = useAuth()
  const [engagements, setEngagements] = useState<AuditEngagement[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [frameworks, setFrameworks] = useState<FrameworkInfo[]>([])
  const [open, setOpen] = useState<OpenEngagement | null>(null)
  const [notice, setNotice] = useState<string | null>(null)

  const load = useCallback(() => {
    setError(null)
    listMyEngagements()
      .then(setEngagements)
      .catch(err => { setError(err?.message ?? 'Could not load your engagements.'); setEngagements(prev => prev ?? []) })
  }, [])

  useEffect(() => { load() }, [load])

  // Framework names are a nicety: an id is shown when the catalog cannot be read.
  useEffect(() => { fetchFrameworks(false).then(setFrameworks).catch(() => {}) }, [])

  const nameById = useMemo(() => Object.fromEntries(frameworks.map(f => [f.id, f.name])), [frameworks])
  const frameworkName = useCallback((id: string) => nameById[id] ?? id, [nameById])

  const openFromNotification = useCallback((target: EngagementReviewTarget) => {
    const engagement = (engagements ?? []).find(e => e.id === target.engagementId)
    if (!engagement) {
      setNotice('That engagement is no longer in your list. Your access to it may have ended.')
      return
    }
    setNotice(null)
    setOpen({ engagement, focus: target.focus ?? null })
  }, [engagements])

  if (engagements === null) {
    return (
      <div className="loading-screen">
        <div className="loading-content">
          <div className="loading-spinner" />
          <div className="loading-text">Loading your engagements</div>
          <div className="loading-subtext">Checking which audit engagements you have access to…</div>
        </div>
      </div>
    )
  }

  // No grant and no organisation: the existing screen explains what to do.
  if (engagements.length === 0 && !error) return <NoOrganisationScreen />

  return (
    <div className="aw">
      <header className="aw-bar">
        <span className="aw-brand">{getAppTitle()}</span>
        <span className="aw-role">Auditor workspace</span>
        <div className="aw-bar-actions">
          <ThemeMenu />
          <NotificationBell onNavigateToEngagementReview={openFromNotification} currentOrgId={null} />
          {user?.email && <span className="aw-user">{user.email}</span>}
          <button type="button" className="aw-signout" onClick={logout}>Sign out</button>
        </div>
      </header>

      {open ? (
        <EngagementReviewWorkspace
          // A notification for another card of the same engagement must reach
          // the board as a new focus, so the focus is part of the key.
          key={`${open.engagement.id}:${open.focus?.reviewId ?? open.focus?.evidenceId ?? open.focus?.controlScfId ?? ''}`}
          organizationId={open.engagement.organization_id}
          engagement={open.engagement}
          frameworkName={frameworkName}
          backLabel="My engagements"
          initialFocus={open.focus}
          onBack={() => { setOpen(null); load() }}
        />
      ) : (
        <main className="aw-main">
          <h1 className="aw-title">My engagements</h1>
          <p className="aw-intro">
            The audit engagements you have been given access to. Open one to review the organisation's
            evidence, move it through the review stages and raise queries.
          </p>

          {notice && <p className="aw-notice" role="status">{notice}</p>}
          {error && (
            <p className="aw-error" role="alert">
              {error} <button type="button" className="aw-retry" onClick={load}>Try again</button>
            </p>
          )}

          <ul className="aw-list">
            {engagements.map(eng => {
              const start = fmtDate(eng.start_date)
              const end = fmtDate(eng.end_date)
              return (
                <li key={eng.id} className="aw-card">
                  <div className="aw-card-main">
                    <div className="aw-card-title-row">
                      <h2 className="aw-card-title">{eng.name}</h2>
                      <span className={`aw-status aw-status-${eng.status}`}>{STATUS_LABELS[eng.status] ?? eng.status}</span>
                    </div>
                    <p className="aw-card-meta">
                      {eng.frameworks.map(frameworkName).join(' · ') || 'No framework'}
                      {' · '}
                      {start && end ? `Audit window ${start} – ${end}` : start ? `From ${start}` : end ? `To ${end}` : 'No audit window set'}
                    </p>
                  </div>
                  <button type="button" className="aw-open" onClick={() => { setNotice(null); setOpen({ engagement: eng, focus: null }) }}>
                    Open review board
                  </button>
                </li>
              )
            })}
          </ul>
        </main>
      )}
    </div>
  )
}
