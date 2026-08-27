/**
 * SystemDetailPage — full-width system detail per SystemDetail.html spec.
 *
 * Promotes SystemsRegistry row click to a full-width detail page.
 * Pattern: hidden-list (VendorManagement idiom) — the registry list stays
 * mounted beneath for filter/pager state; this page renders on top.
 *
 * Layout (top to bottom):
 *   1. Breadcrumb "‹ Systems Registry / <name>" + "k of N systems" pager
 *   2. Header: accent-bar + name + type chip + status chip + vendor + owner + description
 *   3. LINKED CONTROLS badges (evidence_id chips from capabilities)
 *   4. EVIDENCE RECIPES section (recipe generation status + regenerate action)
 *      Deviation: no per-evidence-item recipe cards available client-side via
 *      existing hooks — only recipe generation status. Section shows generation
 *      metadata + regenerate action. Full per-item recipe cards would require
 *      fetching getEvidenceSuggestions per capability (N requests); deferred.
 *   5. ASSOCIATED EVIDENCE table (evidence tracking items whose collecting_system
 *      matches this system's name — existing evidence_tracking endpoint filtered
 *      client-side by collecting_system name; no dedicated by-system endpoint).
 *
 * Keyboard: ArrowLeft→prev, ArrowRight→next, Escape→back.
 * Suppressed when focus is in input/textarea/select/contentEditable.
 */
import { useState, useEffect, useMemo, useCallback } from 'react'
import type { System, SystemEvidenceCapability, RecipeGenerationStatus } from '../types'
import {
  getSystem,
  getSystemCapabilities,
  getEvidenceTracking,
  generateSystemRecipes,
  getRecipeGenerationStatus,
  type EvidenceTracking as ApiEvidenceTracking,
} from '../data/apiClient'

// ─── Helpers ──────────────────────────────────────────────────────────────────

/** True when the keyboard event target should suppress pager shortcuts. */
function isSuppressed(e: KeyboardEvent): boolean {
  const t = e.target
  if (!t || !(t instanceof Element)) return false
  const tag = (t as HTMLElement).tagName?.toLowerCase()
  if (!tag) return false
  if (tag === 'input' || tag === 'textarea' || tag === 'select') return true
  if ((t as HTMLElement).isContentEditable) return true
  return !!document.querySelector('[role="listbox"]')
}

// ─── System type display config ───────────────────────────────────────────────

const SYSTEM_TYPE_LABELS: Record<string, string> = {
  cloud_provider: 'Cloud Provider',
  identity_provider: 'Identity Provider',
  ticketing: 'Ticketing',
  logging: 'Logging',
  security_tool: 'Security Tool',
  code_repository: 'Code Repository',
  document_management: 'Document Mgmt',
  endpoint_management: 'Endpoint Mgmt',
  vulnerability_management: 'Vulnerability Mgmt',
  email_security: 'Email Security',
  security_awareness: 'Security Awareness',
  password_manager: 'Password Manager',
  communication: 'Communication',
  hr_system: 'HR System',
  custom: 'Custom',
}

const STATUS_LABELS: Record<string, string> = {
  active: 'Active',
  inactive: 'Inactive',
  deprecated: 'Deprecated',
}

// ─── Props ────────────────────────────────────────────────────────────────────

export interface SystemDetailPageProps {
  organizationId: string
  systemId: string
  filteredSystems: System[]
  onSystemItemChange: (id: string | null) => void
  onEdit: (system: System) => void
}

// ─── Component ────────────────────────────────────────────────────────────────

export default function SystemDetailPage({
  organizationId,
  systemId,
  filteredSystems,
  onSystemItemChange,
  onEdit,
}: SystemDetailPageProps) {
  const [system, setSystem] = useState<System | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [capabilities, setCapabilities] = useState<SystemEvidenceCapability[]>([])
  const [evidenceTracking, setEvidenceTracking] = useState<ApiEvidenceTracking[]>([])
  const [recipeStatus, setRecipeStatus] = useState<RecipeGenerationStatus | null>(null)
  const [regenerating, setRegenerating] = useState(false)

  // ── Pager ─────────────────────────────────────────────────────────────────

  const currentIndex = useMemo(
    () => filteredSystems.findIndex(s => s.id === systemId),
    [filteredSystems, systemId]
  )

  const total = filteredSystems.length

  const handleBack = useCallback(() => onSystemItemChange(null), [onSystemItemChange])

  const handlePrev = useCallback(() => {
    if (currentIndex > 0) {
      onSystemItemChange(filteredSystems[currentIndex - 1].id)
    }
  }, [currentIndex, filteredSystems, onSystemItemChange])

  const handleNext = useCallback(() => {
    if (currentIndex >= 0 && currentIndex < filteredSystems.length - 1) {
      onSystemItemChange(filteredSystems[currentIndex + 1].id)
    }
  }, [currentIndex, filteredSystems, onSystemItemChange])

  // ── Keyboard shortcuts ────────────────────────────────────────────────────

  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (isSuppressed(e)) return
      if (e.key === 'ArrowRight') handleNext()
      else if (e.key === 'ArrowLeft') handlePrev()
      else if (e.key === 'Escape') handleBack()
    }
    document.addEventListener('keydown', handler)
    return () => document.removeEventListener('keydown', handler)
  }, [handleNext, handlePrev, handleBack])

  // ── Data loading ──────────────────────────────────────────────────────────

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setError(null)

    const load = async () => {
      try {
        const [sys, caps, tracking] = await Promise.all([
          getSystem(systemId, organizationId),
          getSystemCapabilities(systemId, organizationId).catch(() => []),
          getEvidenceTracking(organizationId).catch(() => []),
        ])
        if (cancelled) return
        setSystem(sys)
        setCapabilities(caps)
        setEvidenceTracking(tracking)

        // Recipe generation status (best-effort)
        try {
          const status = await getRecipeGenerationStatus(systemId, organizationId)
          if (!cancelled) setRecipeStatus(status)
        } catch {
          // Not fatal — render without status
        }
      } catch (err) {
        if (!cancelled) setError(err instanceof Error ? err.message : 'Failed to load system')
      } finally {
        if (!cancelled) setLoading(false)
      }
    }

    load()
    return () => { cancelled = true }
  }, [systemId, organizationId])

  // ── Associated evidence filtered by collecting_system name ────────────────

  const associatedEvidence = useMemo(() => {
    if (!system) return []
    return evidenceTracking.filter(
      et => et.collecting_system && et.collecting_system.toLowerCase() === system.name.toLowerCase()
    )
  }, [evidenceTracking, system])

  // ── Regenerate recipes ────────────────────────────────────────────────────

  const handleRegenerate = async () => {
    setRegenerating(true)
    try {
      await generateSystemRecipes(systemId, organizationId)
      // Refresh status
      const status = await getRecipeGenerationStatus(systemId, organizationId)
      setRecipeStatus(status)
    } catch (err) {
      console.error('Failed to generate recipes:', err)
    } finally {
      setRegenerating(false)
    }
  }

  // ── Render states ─────────────────────────────────────────────────────────

  if (loading) {
    return (
      <div className="system-detail-page">
        <div className="system-detail-page-loading">Loading system details…</div>
      </div>
    )
  }

  if (error || !system) {
    return (
      <div className="system-detail-page">
        <div className="system-detail-page-error">
          <button
            onClick={handleBack}
            className="system-detail-breadcrumb-back"
            type="button"
          >
            ‹ Systems Registry
          </button>
          <p>{error || 'System not found'}</p>
        </div>
      </div>
    )
  }

  const typeLabel = SYSTEM_TYPE_LABELS[system.system_type] || system.system_type
  const statusLabel = STATUS_LABELS[system.status] || system.status
  const pagerText = currentIndex >= 0
    ? `${currentIndex + 1} of ${total} systems`
    : `— of ${total} systems`

  const prevDisabled = currentIndex <= 0
  const nextDisabled = currentIndex < 0 || currentIndex >= filteredSystems.length - 1

  return (
    <div className="system-detail-page">

      {/* ── Breadcrumb + pager ─────────────────────────────────────────── */}
      <div className="system-detail-breadcrumb-strip">
        <button
          onClick={handleBack}
          className="system-detail-breadcrumb-back"
          type="button"
          aria-label="Systems Registry"
        >
          <svg width="14" height="14" viewBox="0 0 14 14" fill="none" aria-hidden="true">
            <path d="M9 2L4 7l5 5" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round"/>
          </svg>
          Systems Registry
        </button>
        <span className="system-detail-breadcrumb-sep">/</span>
        <span className="system-detail-breadcrumb-name">{system.name}</span>

        <div className="system-detail-pager">
          <span className="system-detail-pager-count">{pagerText}</span>
          <div className="system-detail-pager-buttons">
            <button
              onClick={handlePrev}
              disabled={prevDisabled}
              aria-label="previous"
              className="system-detail-pager-btn"
              type="button"
            >
              <svg width="12" height="12" viewBox="0 0 14 14" fill="none" aria-hidden="true">
                <path d="M9 2L4 7l5 5" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round"/>
              </svg>
            </button>
            <button
              onClick={handleNext}
              disabled={nextDisabled}
              aria-label="next"
              className="system-detail-pager-btn"
              type="button"
            >
              <svg width="12" height="12" viewBox="0 0 14 14" fill="none" aria-hidden="true">
                <path d="M5 2l5 5-5 5" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round"/>
              </svg>
            </button>
          </div>
        </div>
      </div>

      {/* ── Page body ──────────────────────────────────────────────────── */}
      <div className="system-detail-page-body">

        {/* ── Header block ─────────────────────────────────────────────── */}
        <div className="system-detail-page-header">
          <div className="system-detail-page-chips-row">
            <div className="system-detail-page-tick-bar" aria-hidden="true" />
            <h1 className="system-detail-page-name">{system.name}</h1>
            <span className="system-detail-page-chip">{typeLabel}</span>
            <span className={`system-detail-page-chip system-detail-status-${system.status}`}>
              {statusLabel}
            </span>
            <div className="system-detail-page-meta">
              {system.vendor && (
                <span className="system-detail-page-meta-item">
                  Vendor — <span className="system-detail-page-meta-value">{system.vendor}</span>
                </span>
              )}
            </div>
            <div className="system-detail-page-actions">
              <button
                onClick={() => onEdit(system)}
                className="system-detail-edit-btn"
                type="button"
                aria-label="Edit system"
              >
                Edit
              </button>
            </div>
          </div>

          {(system.description || system.category) && (
            <p className="system-detail-page-description">
              {system.description}
              {system.category && ` Category: ${system.category}.`}
            </p>
          )}

          {/* ── Linked controls ────────────────────────────────────────── */}
          {capabilities.length > 0 && (
            <div className="system-detail-linked-controls">
              <span className="system-detail-section-label">LINKED CONTROLS</span>
              <div className="system-detail-control-badges">
                {capabilities.slice(0, 8).map(cap => (
                  <span key={cap.id} className="system-detail-control-badge">
                    {cap.evidence_id}
                  </span>
                ))}
                {capabilities.length > 8 && (
                  <span className="system-detail-control-badge-more">
                    +{capabilities.length - 8} more
                  </span>
                )}
              </div>
            </div>
          )}
        </div>

        {/* ── Evidence recipes ──────────────────────────────────────────── */}
        <div className="system-detail-recipes-section">
          <div className="system-detail-recipes-header">
            <span className="system-detail-section-label">EVIDENCE RECIPES</span>
            {recipeStatus?.updated_at && (
              <span className="system-detail-recipes-meta">
                Generated {new Date(recipeStatus.updated_at).toLocaleDateString('en-GB', {
                  day: 'numeric', month: 'short', year: 'numeric',
                })}
                {recipeStatus.status === 'completed' && ' · via system catalog'}
              </span>
            )}
            {!recipeStatus && (
              <span className="system-detail-recipes-deviation">
                Deviation: recipe generation status unavailable
              </span>
            )}
            <button
              onClick={handleRegenerate}
              disabled={regenerating}
              className="system-detail-regenerate-btn"
              type="button"
              aria-label="Regenerate recipes"
            >
              {regenerating ? 'Regenerating…' : 'Regenerate recipes'}
            </button>
          </div>

          {recipeStatus?.status === 'running' && (
            <div className="system-detail-recipe-running">
              Recipe generation in progress…
            </div>
          )}
          {recipeStatus?.status === 'failed' && (
            <div className="system-detail-recipe-failed">
              Recipe generation failed: {recipeStatus.error || 'unknown error'}
            </div>
          )}
          {recipeStatus?.status === 'completed' && (
            <p className="system-detail-recipe-completed-note">
              Recipes have been generated for this system. Open individual evidence items to view per-item collection guidance.
            </p>
          )}
          {(!recipeStatus || recipeStatus.status === 'idle') && (
            <p className="system-detail-recipe-idle-note">
              No recipes generated yet. Click "Regenerate recipes" to create collection guidance for this system's evidence items.
            </p>
          )}
        </div>

        {/* ── Associated evidence ───────────────────────────────────────── */}
        {associatedEvidence.length > 0 && (
          <div className="system-detail-evidence-section">
            <div className="system-detail-section-label">
              ASSOCIATED EVIDENCE · {associatedEvidence.length} ITEMS
            </div>
            <div className="system-detail-evidence-table">
              {associatedEvidence.map((et, i) => (
                <div key={i} className="system-detail-evidence-row">
                  <span className="system-detail-evidence-method">
                    {et.method_of_collection || '—'}
                  </span>
                  <span className="system-detail-evidence-freq">
                    {et.frequency || '—'}
                  </span>
                  <span className={`system-detail-evidence-tracked ${et.is_tracked ? 'system-detail-evidence-tracked--yes' : ''}`}>
                    {et.is_tracked ? 'Tracked' : 'Untracked'}
                  </span>
                </div>
              ))}
            </div>
          </div>
        )}

      </div>
    </div>
  )
}
