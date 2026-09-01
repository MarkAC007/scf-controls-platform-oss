/**
 * EvidenceDetailPage — full-width evidence detail view with breadcrumb + prev/next pager.
 *
 * Promoted from the `.detail` pane inside EvidenceReview (Phase 4, Task 3).
 * All detail-pane content ported verbatim; the URL mechanism is unchanged —
 * selectEvidence/pushSearch/withEvidenceItem/popstate/auto-select all live in
 * EvidenceReview and are not touched here.
 *
 * Keyboard: ArrowLeft→onPrev, ArrowRight→onNext, Escape→onBack.
 * Suppressed when focus is in input/textarea/select/contentEditable.
 * Suppressed when a dropdown overlay (.theme-menu-panel, .user-dropdown-menu) is open.
 */
import { useEffect, type JSX } from 'react'
import type {
  EvidenceId,
  EvidenceTracking,
  ScopedControlsFile,
  CollectionGuidanceResponse,
  RecipeConfidence,
  EvidenceTemplatesFile,
  ERLFile,
  MemberType,
} from '../../types'
import type { System, EvidenceSuggestionsResponse, UserSimple } from '../../types'
import type { EnrichedControl } from '../../types'
import { getScopedControl } from '../../data/scopingService'
import { MaturityBadge, MaturityStepper, MaturityAdvisoryCard } from '../maturity'
import {
  RecipeCard,
  RecipeConfidenceBadge,
  EvidenceTemplateGuidance,
  EvidenceFileUpload,
  EvidenceFileList,
  EvidenceAssigneeSelect,
  UntrackedUploadNotice,
} from '../evidence'
import { WindowReviewPanel } from '../evidence/WindowReviewPanel'
import { AssignmentPicker } from '../AssignmentPicker'
import OwningTeams from '../OwningTeams'
import { ModernCommentThread } from '../ModernCommentThread'
import { EvidenceTaskList } from '../EvidenceTaskList'
import { ScfReference } from '../provenance/ScfReference'
import { frequencyOptionsFor } from '../../data/frequencyVocabulary'
import { PER_WINDOW_REVIEW_ENABLED } from '../../data/featureFlags'
import { getEvidenceTracking } from '../../data/scopingService'
import { useIsOrgEditor } from '../../hooks/useHasOrgRole'

// ─── Types ────────────────────────────────────────────────────────────────────

export interface EvidenceDetailPageProps {
  /** The evidence item being shown. */
  evidenceItem: { id: EvidenceId; title: string; domain: string; controlCount: number }
  /** Local tracking state for the item (may be empty {}). */
  tracking: Partial<EvidenceTracking>
  /** Controls that require this evidence item. */
  requiringControls: EnrichedControl[]
  /**
   * null  → total unknown (pager fully hidden)
   * { index: null, total } → not in filtered set, "— of N", both buttons disabled
   * { index: number, total } → normal pager
   */
  position: { index: number | null; total: number } | null
  onPrev: () => void
  onNext: () => void
  /** Called when the user presses Esc or the back button. */
  onBack: () => void

  // ── Data dependencies (passed through from EvidenceReview) ────────────────
  scopingData: ScopedControlsFile
  systems: System[]
  orgMembers: UserSimple[]
  memberTypeOf: (userId: string | null | undefined) => MemberType | undefined
  suggestions: EvidenceSuggestionsResponse | null
  loadingSuggestions: boolean
  collectionGuidance: CollectionGuidanceResponse | null
  loadingGuidance: boolean
  feedbackSubmitted: string | null
  fileListRefreshTrigger: number
  saving: boolean
  canManageTeams: boolean
  erlData?: ERLFile
  evidenceTemplates?: EvidenceTemplatesFile

  // ── Callbacks ──────────────────────────────────────────────────────────────
  onUpdateTracking: (evidenceId: EvidenceId, field: keyof EvidenceTracking, value: string | boolean) => void
  onRecipeFeedback: (feedbackType: 'helpful' | 'not_matching') => void
  onFileUploaded: () => void
  onReloadTeamAssignments: () => void
  /** Navigate to a control in control-first view mode. */
  onNavigateToControl: (controlId: string) => void
}

// ─── Helpers ──────────────────────────────────────────────────────────────────

/** True when the keyboard event target should suppress pager shortcuts. */
function isSuppressed(e: KeyboardEvent): boolean {
  const t = e.target
  if (!t || !(t instanceof Element)) return false
  const tag = (t as HTMLElement).tagName?.toLowerCase()
  if (!tag) return false
  if (tag === 'input' || tag === 'textarea' || tag === 'select') return true
  if ((t as HTMLElement).isContentEditable) return true
  return false
}

// ─── Component ────────────────────────────────────────────────────────────────

export default function EvidenceDetailPage({
  evidenceItem,
  tracking,
  requiringControls,
  position,
  onPrev,
  onNext,
  onBack,
  scopingData,
  systems,
  orgMembers,
  memberTypeOf,
  suggestions,
  collectionGuidance,
  loadingGuidance,
  feedbackSubmitted,
  fileListRefreshTrigger,
  saving,
  canManageTeams,
  erlData = {},
  evidenceTemplates = {},
  onUpdateTracking,
  onRecipeFeedback,
  onFileUploaded,
  onReloadTeamAssignments,
  onNavigateToControl,
}: EvidenceDetailPageProps): JSX.Element {
  const isTracked = tracking.is_tracked || false

  // Who may review a file or queue an AI assessment: an editor of this
  // organisation, which is the rank the backend enforces on both
  // (``require_org_role("editor")`` on the per-file review and assess-bulk
  // routes). Until this landed the review buttons had no way to switch on and
  // were dead for every user — the prop was optional and nothing passed it.
  //
  // A courtesy gate, not a boundary: the API refuses the write regardless.
  // Derived here rather than in ``EvidenceReview`` — the other permission on
  // this page (``canManageTeams``) arrives as a prop, and that remains the
  // grain for anything a sibling screen also needs.
  const canReviewFiles = useIsOrgEditor(scopingData.organizationId)

  // ── Keyboard shortcuts ──────────────────────────────────────────────────────
  useEffect(() => {
    function handleKeyDown(e: KeyboardEvent): void {
      if (isSuppressed(e)) return
      if (
        e.key === 'Escape' &&
        document.querySelector('.theme-menu-panel, .user-dropdown-menu')
      ) return

      if (e.key === 'ArrowLeft') {
        e.preventDefault()
        onPrev()
      } else if (e.key === 'ArrowRight') {
        e.preventDefault()
        onNext()
      } else if (e.key === 'Escape') {
        e.preventDefault()
        onBack()
      }
    }
    window.addEventListener('keydown', handleKeyDown)
    return () => window.removeEventListener('keydown', handleKeyDown)
  }, [onPrev, onNext, onBack])

  // ── Pager state ─────────────────────────────────────────────────────────────
  const isFirst = position === null || position.index === null || position.index === 0
  const isLast =
    position === null ||
    position.index === null ||
    position.index === position.total - 1
  const positionText =
    position === null
      ? null
      : position.index === null
        ? `— of ${position.total}`
        : `${position.index + 1} of ${position.total}`

  const evidenceTracking = getEvidenceTracking(scopingData, evidenceItem.id)
  const evidenceDbId = evidenceTracking?.id

  // ── Render ───────────────────────────────────────────────────────────────────
  return (
    <div className="evidence-detail-page">
      {/* ── Breadcrumb / pager bar ─────────────────────────────────────────── */}
      <div className="control-detail-breadcrumb" data-testid="evidence-detail-breadcrumb">
        <button
          className="control-detail-back-btn"
          onClick={onBack}
          aria-label="Back to Evidence"
        >
          <svg
            className="control-detail-back-icon"
            width="14"
            height="14"
            viewBox="0 0 14 14"
            fill="none"
            aria-hidden="true"
          >
            <path
              d="M9 2L4 7l5 5"
              stroke="currentColor"
              strokeWidth="1.6"
              strokeLinecap="round"
              strokeLinejoin="round"
            />
          </svg>
          Evidence
        </button>
        <span className="control-detail-breadcrumb-sep">/</span>
        <span className="control-detail-breadcrumb-id" data-testid="evidence-detail-id">
          {evidenceItem.id}
        </span>

        <div className="control-detail-pager" data-testid="evidence-detail-pager">
          {positionText && (
            <span className="control-detail-position" data-testid="evidence-detail-position">
              {positionText}
            </span>
          )}
          <div className="control-detail-pager-buttons">
            <button
              className="control-detail-pager-btn"
              onClick={onPrev}
              disabled={isFirst}
              aria-label="Previous evidence item"
            >
              <svg width="12" height="12" viewBox="0 0 14 14" fill="none" aria-hidden="true">
                <path
                  d="M9 2L4 7l5 5"
                  stroke="currentColor"
                  strokeWidth="1.6"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                />
              </svg>
            </button>
            <button
              className="control-detail-pager-btn"
              onClick={onNext}
              disabled={isLast}
              aria-label="Next evidence item"
            >
              <svg width="12" height="12" viewBox="0 0 14 14" fill="none" aria-hidden="true">
                <path
                  d="M5 2l5 5-5 5"
                  stroke="currentColor"
                  strokeWidth="1.6"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                />
              </svg>
            </button>
          </div>
        </div>
      </div>

      {/* ── Scrollable body ────────────────────────────────────────────────── */}
      <div className="evidence-detail-body">
        {/* ── Header ─────────────────────────────────────────────────────────── */}
        <div className="detail-header-compact">
          <div className="detail-header-main surface-bedrock" data-source="SCF Evidence Requirements">
            <span className="scf-source-tag">SCF ERL</span>
            <div className="detail-id-compact">{evidenceItem.id}</div>
            <h2 className="detail-name-compact">{evidenceItem.title}</h2>
            <div className="detail-meta-row">
              <span className="detail-domain-compact">{evidenceItem.domain}</span>
              <div className="detail-badges">
                {isTracked ? (
                  <span className="badge-theme theme-process">Tracked</span>
                ) : (
                  <span className="badge-type type-detective">Not Tracked</span>
                )}
                {tracking.maturity_level && (
                  <MaturityBadge level={tracking.maturity_level} size="small" />
                )}
                {saving && <span className="detail-save-chip">Saving…</span>}
              </div>
            </div>
          </div>
        </div>

        <div className="detail-content-compact">
          {/* ── Evidence Guidance ────────────────────────────────────────────── */}
          <ScfReference>
            <EvidenceTemplateGuidance
              evidenceId={evidenceItem.id}
              evidenceTemplates={evidenceTemplates}
              orgId={scopingData.organizationId}
              erlData={erlData}
              tracking={evidenceTracking}
            />
          </ScfReference>

          {/*
            Required by Controls, as a strip rather than a section. It is
            provenance — why this item is on the list at all — and it was taking
            a full card's height to say something nobody comes to this page to
            read. The chips still navigate.
          */}
          <ScfReference>
            <div className="detail-section-container evidence-required-strip">
              <div className="evidence-required-strip-row">
                <span className="evidence-required-strip-label">Required by Controls</span>
                <span className="container-count">{requiringControls.length}</span>
                {requiringControls.length === 0 ? (
                  <p className="muted evidence-required-strip-empty">No controls require this evidence</p>
                ) : (
                  <div className="requiring-controls-pills">
                    {requiringControls.map(ctrl => {
                      const tooltipId = `tooltip-ev-${evidenceItem.id}-${ctrl.scf_id}`
                      const ctrlScopedData = getScopedControl(scopingData, ctrl.scf_id)
                      const implStatus = ctrlScopedData?.implementation_status || 'not_started'

                      const statusConfig = {
                        implemented: { label: 'IMPLEMENTED', icon: '✅', class: 'status-implemented' },
                        in_progress: { label: 'IN PROGRESS', icon: '🔄', class: 'status-in-progress' },
                        not_started: { label: 'NOT STARTED', icon: '⭕', class: 'status-not-started' },
                        at_risk: { label: 'AT RISK', icon: '⚠️', class: 'status-at-risk' },
                        not_applicable: { label: 'NOT APPLICABLE', icon: '❌', class: 'status-not-applicable' },
                        deferred: { label: 'DEFERRED', icon: '⏸️', class: 'status-deferred' },
                      }
                      const status = statusConfig[implStatus as keyof typeof statusConfig] || statusConfig.not_started
                      const pillStatusClass =
                        implStatus === 'not_applicable' ? 'pill-not-applicable' :
                        implStatus === 'deferred' ? 'pill-deferred' :
                        implStatus === 'at_risk' ? 'pill-at-risk' : ''

                      return (
                        <div key={ctrl.scf_id} className="control-pill-wrapper">
                          <button
                            className={`control-pill ${pillStatusClass}`}
                            onClick={() => onNavigateToControl(ctrl.scf_id)}
                            onMouseEnter={(e) => {
                              const tooltip = document.getElementById(tooltipId)
                              if (tooltip) {
                                const rect = e.currentTarget.getBoundingClientRect()
                                tooltip.style.top = `${rect.top - tooltip.offsetHeight - 8}px`
                                tooltip.style.left = `${Math.max(10, rect.left + rect.width / 2 - 200)}px`
                              }
                            }}
                          >
                            {ctrl.scf_id} — {ctrl.control_name}
                          </button>
                          <div id={tooltipId} className="control-tooltip">
                            <div className="tooltip-header">
                              <strong>{ctrl.scf_id}</strong> — {ctrl.control_name}
                            </div>
                            <div className="tooltip-domain">{ctrl.scf_domain}</div>
                            {ctrlScopedData && status && (
                              <div className={`tooltip-status-box ${status.class}`}>
                                <div className="status-row">
                                  <span className="status-label">Status:</span>
                                  <span className="status-value">{status.icon} {status.label}</span>
                                </div>
                                {ctrlScopedData.owner && (
                                  <div className="status-row">
                                    <span className="status-label">Owner:</span>
                                    <span className="status-value">{ctrlScopedData.owner}</span>
                                  </div>
                                )}
                                {ctrlScopedData.completion_date && (
                                  <div className="status-row">
                                    <span className="status-label">Target Date:</span>
                                    <span className="status-value">{ctrlScopedData.completion_date}</span>
                                  </div>
                                )}
                              </div>
                            )}
                            <div className="tooltip-section">
                              <strong>Description:</strong>
                              <p>{ctrl.control_description}</p>
                            </div>
                          </div>
                        </div>
                      )
                    })}
                  </div>
                )}
              </div>
            </div>
          </ScfReference>

          {/* ── Collection Record Form ─────────────────────────────────────────── */}
          <div className="detail-section-container surface-bench">
            <div className="container-header bench-header">
              <span className="container-icon">📋</span>
              <span className="container-title">Your Collection Record</span>
              {isTracked && <span className="container-tracking-badge">✓ Active</span>}
            </div>
            <div className="container-content">
              {/* Tracking toggle */}
              <div className="tracking-toggle-section">
                <label className="tracking-toggle-label">
                  <input
                    type="checkbox"
                    checked={isTracked}
                    onChange={e => onUpdateTracking(evidenceItem.id, 'is_tracked', e.target.checked)}
                    className="tracking-checkbox"
                  />
                  <div className="tracking-toggle-content">
                    <div className="tracking-toggle-title">Evidence Collection Active</div>
                    <div className="tracking-toggle-hint">Mark this evidence as being actively collected for compliance</div>
                  </div>
                </label>
              </div>

              {/* Collecting System with suggestions */}
              <div className="form-group">
                <label>Collecting System</label>
                {(() => {
                  const suggestedNames = new Set(
                    (suggestions?.capable_systems || []).map(s => s.name)
                  )
                  const suggestedSystems = systems.filter(s => suggestedNames.has(s.name))
                  const otherSystems = systems.filter(s => !suggestedNames.has(s.name))
                  return (
                    <select
                      value={tracking.collecting_system || ''}
                      onChange={e => onUpdateTracking(evidenceItem.id, 'collecting_system', e.target.value)}
                      className="form-control"
                    >
                      <option value="">Select System...</option>
                      {suggestedSystems.length > 0 && (
                        <optgroup label="Suggested for this evidence">
                          {suggestedSystems.map(system => {
                            const cap = suggestions?.capable_systems.find(s => s.name === system.name)
                            return (
                              <option key={system.id} value={system.name}>
                                {system.name} ({system.vendor || system.system_type}){cap ? ` — ${cap.capability_status}` : ''}
                              </option>
                            )
                          })}
                        </optgroup>
                      )}
                      {otherSystems.length > 0 && (
                        <optgroup label="All systems">
                          {otherSystems.map(system => (
                            <option key={system.id} value={system.name}>
                              {system.name} ({system.vendor || system.system_type})
                            </option>
                          ))}
                        </optgroup>
                      )}
                      <optgroup label="Other">
                        <option value="Manual">Manual / Not Automated</option>
                      </optgroup>
                    </select>
                  )
                })()}
                {suggestions?.recommendation && !tracking.collecting_system && (
                  <div className="form-hint suggestion-inline-hint">
                    {'✨'} Recommended: <strong>{suggestions.recommendation.system_name}</strong> — {suggestions.recommendation.reason}
                  </div>
                )}
              </div>

              {/* Collection Maturity */}
              <div className="form-group">
                <label>Collection Maturity</label>
                <MaturityStepper
                  value={tracking.maturity_level}
                  onChange={level => onUpdateTracking(evidenceItem.id, 'maturity_level', level)}
                />
              </div>

              {/* Inline Collection Guide */}
              {tracking.collecting_system && tracking.collecting_system !== 'Manual' && (
                <div className="inline-collection-guide">
                  {loadingGuidance ? (
                    <div className="inline-guide-loading">Loading collection guide...</div>
                  ) : collectionGuidance?.recipe ? (
                    <details className="inline-guide-details" open>
                      <summary className="inline-guide-summary">
                        <span className="inline-guide-icon">{'📖'}</span>
                        <span>Collection Guide for {collectionGuidance.system_name}</span>
                        <RecipeConfidenceBadge confidence={collectionGuidance.recipe_confidence as RecipeConfidence} />
                      </summary>
                      <div className="inline-guide-content">
                        <RecipeCard
                          recipe={collectionGuidance.recipe}
                          confidence={collectionGuidance.recipe_confidence as RecipeConfidence}
                        />
                        <div className="recipe-feedback">
                          {feedbackSubmitted ? (
                            <div className="recipe-feedback-thanks">
                              {'✅'} Thanks for your feedback!
                            </div>
                          ) : (
                            <>
                              <span className="recipe-feedback-label">Was this helpful?</span>
                              <button
                                className="recipe-feedback-btn recipe-feedback-yes"
                                onClick={() => onRecipeFeedback('helpful')}
                              >
                                {'👍'} This helped
                              </button>
                              <button
                                className="recipe-feedback-btn recipe-feedback-no"
                                onClick={() => onRecipeFeedback('not_matching')}
                              >
                                {'👎'} Didn't match
                              </button>
                            </>
                          )}
                        </div>
                      </div>
                    </details>
                  ) : collectionGuidance && !collectionGuidance.recipe ? (
                    <div className="inline-guide-empty">
                      <span className="inline-guide-icon">{'📖'}</span>
                      No collection recipe available for {collectionGuidance.system_name} at {collectionGuidance.current_maturity}.
                    </div>
                  ) : null}
                </div>
              )}

              {/* Maturity Advisory */}
              {tracking.maturity_level && (
                <MaturityAdvisoryCard
                  currentLevel={tracking.maturity_level}
                  evidenceId={evidenceItem.id}
                  evidenceTitle={evidenceItem.title}
                  nextLevelRecipe={collectionGuidance?.next_level_preview || undefined}
                  systemName={collectionGuidance?.system_name || undefined}
                />
              )}

              {/* Method of Collection */}
              <div className="form-group">
                <label>Method of Collection</label>
                <input
                  type="text"
                  value={tracking.method_of_collection || ''}
                  onChange={e => onUpdateTracking(evidenceItem.id, 'method_of_collection', e.target.value)}
                  placeholder="e.g., Automated export, Manual review, Screenshot"
                  className="form-control"
                />
              </div>

              {/* Frequency */}
              <div className="form-row">
                <div className="form-group">
                  <label>Frequency</label>
                  <select
                    value={tracking.frequency || ''}
                    onChange={e => onUpdateTracking(evidenceItem.id, 'frequency', e.target.value)}
                    className="form-control"
                  >
                    <option value="">Not set</option>
                    {frequencyOptionsFor(tracking.frequency).map(opt => (
                      <option key={opt.value} value={opt.value}>{opt.label}</option>
                    ))}
                  </select>
                </div>
              </div>

              {/* Assignee */}
              <div className="form-row">
                <EvidenceAssigneeSelect
                  id={`assignee-${evidenceItem.id}`}
                  value={tracking.assigned_user_id}
                  resolved={tracking.assigned_user}
                  members={orgMembers}
                  memberTypeOf={memberTypeOf}
                  onChange={userId =>
                    onUpdateTracking(evidenceItem.id, 'assigned_user_id', userId)
                  }
                />
              </div>

              {/* Comments */}
              <div className="form-group">
                <label>Comments</label>
                <textarea
                  value={tracking.comments || ''}
                  onChange={e => onUpdateTracking(evidenceItem.id, 'comments', e.target.value)}
                  placeholder="Additional notes about evidence collection..."
                  className="form-control"
                  rows={3}
                />
              </div>
            </div>
          </div>

          {/*
            Tasks, unconditionally. The card used to disappear with the rest of
            the collaboration block until a tracking row existed, so the one
            state that needs to be told what to do next was the state that got
            no card at all. It renders disabled instead, and says why.
          */}
          <EvidenceTaskList
            evidenceTrackingId={evidenceDbId ?? ''}
            evidenceId={evidenceItem.id}
            organizationId={scopingData.organizationId ?? ''}
            onTaskChange={() => {}}
            disabled={!evidenceDbId || !scopingData.organizationId}
          />

          {/* ── Evidence Files ─────────────────────────────────────────────────── */}
          {scopingData.organizationId && (
            <div className="detail-section-container surface-bench">
              <div className="container-header bench-header">
                <span className="container-icon">{'📁'}</span>
                <span className="container-title">Your Evidence Files</span>
              </div>
              <div className="container-content">
                {!isTracked && (
                  <UntrackedUploadNotice
                    onStartTracking={() =>
                      onUpdateTracking(evidenceItem.id, 'is_tracked', true)
                    }
                  />
                )}
                <EvidenceFileUpload
                  orgId={scopingData.organizationId}
                  evidenceId={evidenceItem.id}
                  onUploadComplete={onFileUploaded}
                />
                <EvidenceFileList
                  orgId={scopingData.organizationId}
                  evidenceId={evidenceItem.id}
                  refreshTrigger={fileListRefreshTrigger}
                  canReview={canReviewFiles}
                  canAssess={canReviewFiles}
                />
              </div>
            </div>
          )}

          {/* ── Window Review Panel (flag-gated) ─────────────────────────────── */}
          {scopingData.organizationId && PER_WINDOW_REVIEW_ENABLED && (
            <WindowReviewPanel
              orgId={scopingData.organizationId}
              evidenceId={evidenceItem.id}
              refreshTrigger={fileListRefreshTrigger}
            />
          )}

          {/* ── Assignments, Teams, Comments ─────────────────────────────────── */}
          {evidenceDbId && scopingData.organizationId ? (
            <div className="evidence-collaboration-container">
              {/* Assignments */}
              <div className="evidence-collaboration-section">
                <AssignmentPicker
                  organizationId={scopingData.organizationId}
                  assignableType="evidence"
                  assignableId={evidenceDbId}
                  label="Collaborators"
                  onAssignmentChange={() => {}}
                />
              </div>

              {/* Owning teams */}
              <div className="evidence-collaboration-section">
                <OwningTeams
                  organizationId={scopingData.organizationId}
                  assignableType="evidence"
                  assignableId={evidenceDbId}
                  canManage={canManageTeams}
                  onChange={() => { void onReloadTeamAssignments() }}
                />
              </div>

              {/* Comment thread */}
              <div className="evidence-collaboration-section">
                <ModernCommentThread
                  commentableType="evidence"
                  commentableId={evidenceDbId}
                  organizationId={scopingData.organizationId}
                />
              </div>
            </div>
          ) : (
            <div className="evidence-save-hint">
              <p>
                Save this evidence tracking to enable tasks, assignments and comments
              </p>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
