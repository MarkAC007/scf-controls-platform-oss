/**
 * AssessmentEngineSettings — Settings → AI assessment engine (organisation admin).
 *
 * Which engine judges this organisation's evidence: Claude, Claude with Jev
 * running in the shadow, or Jev. The choice is an org setting (PATCH
 * /settings, admin only); the status card beside it — whether a TypeSafe key
 * exists, and how the shadow has compared so far — is readable by any member.
 *
 * The shadow comparison is the evidence for a cutover, so it only states
 * numbers it has: before the first comparison the rates are null, and the card
 * says so rather than printing a row of 0%.
 */
import { useCallback, useEffect, useState } from 'react'
import { toast } from 'react-hot-toast'

import { updateOrganizationSettings } from '../data/apiClient'
import { getAssessmentEngineStatus } from '../data/assessmentEngineApi'
import type { AssessmentEngine, AssessmentEngineStatus, ShadowStats } from '../data/assessmentEngineApi'
import { useOrganizationSettings } from '../hooks/useOrganizationSettings'
import { useIsOrgAdmin } from '../hooks/useIsOrgAdmin'

interface AssessmentEngineSettingsProps {
  organizationId: string
}

const ENGINE_OPTIONS: { value: AssessmentEngine; label: string; description: string }[] = [
  {
    value: 'llm',
    label: 'Claude (LLM)',
    description: 'Full written rationale per objective. Default.',
  },
  {
    value: 'jev_shadow',
    label: 'Claude with Jev shadow',
    description:
      'Claude decides; Jev assesses the same objectives in the background so the two can be compared before any cutover.',
  },
  {
    value: 'jev',
    label: 'Jev (System One)',
    description:
      'Jev decides. Fast, calibrated per-objective designations with a confidence figure; no written rationale.',
  },
]

function messageOf(err: unknown, fallback: string): string {
  return err instanceof Error && err.message ? err.message : fallback
}

/** A 0..1 rate as a whole percentage; an em dash when there is no rate. */
function percent(rate: number | null): string {
  return rate === null ? '—' : `${Math.round(rate * 100)}%`
}

/** Cents as cents while they are small, dollars once they are not. */
function formatCost(cents: number | null): string {
  if (cents === null) return '—'
  if (cents >= 100) return `$${(cents / 100).toFixed(2)}`
  if (cents > 0 && cents < 0.01) return '<0.01¢'
  return `${cents.toFixed(2)}¢`
}

/** Human timestamp; falls back to the raw string if it will not parse. */
function formatWhen(iso: string | null): string {
  if (!iso) return '—'
  const parsed = new Date(iso)
  return Number.isNaN(parsed.getTime()) ? iso : parsed.toLocaleString()
}

function ShadowStatsBlock({ stats, cutoff }: { stats: ShadowStats; cutoff: number }) {
  if (stats.compared_verdicts === 0) {
    return (
      <p className="settings-card-sub assessment-engine-muted">
        No shadow comparisons yet — enable the shadow mode and assess some evidence.
      </p>
    )
  }

  const rows: [string, string][] = [
    ['Compared verdicts', String(stats.compared_verdicts)],
    ['Failed', String(stats.failed_verdicts)],
    [
      'Objective agreement',
      `${stats.objectives_agreed}/${stats.objectives_compared} (${percent(stats.agreement_rate)})`,
    ],
    [
      `Confident agreement (≥ ${Math.round(cutoff * 100)}%)`,
      `${stats.confident_agreed}/${stats.confident_objectives} (${percent(stats.confident_agreement_rate)})`,
    ],
    ['Status agreement', percent(stats.status_agreement_rate)],
    ['Mean latency', stats.mean_latency_ms === null ? '—' : `${Math.round(stats.mean_latency_ms)} ms`],
    ['Total cost', formatCost(stats.total_cost_cents)],
    ['Last compared', formatWhen(stats.last_compared_at)],
  ]

  return (
    <div className="assessment-engine-stats" data-testid="assessment-engine-shadow-stats">
      <h3>Shadow comparison</h3>
      <dl>
        {rows.map(([label, value]) => (
          <div key={label} className="assessment-engine-stat">
            <dt>{label}</dt>
            <dd>{value}</dd>
          </div>
        ))}
      </dl>
    </div>
  )
}

export default function AssessmentEngineSettings({ organizationId }: AssessmentEngineSettingsProps) {
  const { data: orgSettings, refetch: refetchSettings } = useOrganizationSettings(organizationId)
  const isAdmin = useIsOrgAdmin(organizationId)

  const savedEngine: AssessmentEngine = orgSettings?.evidence_assessment_engine ?? 'llm'
  const [selected, setSelected] = useState<AssessmentEngine>(savedEngine)
  const [saving, setSaving] = useState(false)

  const [status, setStatus] = useState<AssessmentEngineStatus | null>(null)
  const [statusError, setStatusError] = useState<string | null>(null)

  useEffect(() => {
    setSelected(savedEngine)
  }, [savedEngine])

  const loadStatus = useCallback(async () => {
    try {
      setStatus(await getAssessmentEngineStatus(organizationId))
      setStatusError(null)
    } catch (err) {
      setStatusError(messageOf(err, 'Failed to load the assessment engine status'))
    }
  }, [organizationId])

  useEffect(() => {
    loadStatus()
  }, [loadStatus])

  const handleSave = useCallback(async () => {
    setSaving(true)
    try {
      await updateOrganizationSettings(organizationId, { evidence_assessment_engine: selected })
      toast.success('Assessment engine saved')
      await Promise.all([refetchSettings(), loadStatus()])
    } catch (err) {
      toast.error(messageOf(err, 'Failed to save the assessment engine'))
    } finally {
      setSaving(false)
    }
  }, [organizationId, selected, refetchSettings, loadStatus])

  // Either side counts: a Jev mode about to be saved, or one already in force
  // while an admin looks at switching away from it.
  const jevInPlay = selected !== 'llm' || savedEngine !== 'llm'
  const keyMissing = status !== null && !status.typesafe_key_configured && jevInPlay

  return (
    <div className="settings-card assessment-engine-settings">
      <h2>AI assessment engine</h2>
      <p className="settings-card-sub">
        Which model judges this organisation&rsquo;s evidence against its assessment objectives.
      </p>

      <fieldset
        className="assessment-engine-options"
        aria-label="Assessment engine"
        disabled={!isAdmin || saving}
      >
        {ENGINE_OPTIONS.map(option => (
          <label
            key={option.value}
            className={`assessment-engine-option${selected === option.value ? ' selected' : ''}`}
          >
            <input
              type="radio"
              name="assessment-engine"
              value={option.value}
              checked={selected === option.value}
              onChange={() => setSelected(option.value)}
            />
            <span className="assessment-engine-option-text">
              <span className="assessment-engine-option-label">{option.label}</span>
              <span className="assessment-engine-option-desc">{option.description}</span>
            </span>
          </label>
        ))}
      </fieldset>

      {keyMissing && (
        <div
          className="integration-banner integration-banner-warning"
          data-testid="assessment-engine-key-warning"
          role="status"
        >
          TypeSafe API key is not configured. Set it under Integrations (platform admin) or as{' '}
          <code>TYPESAFE_API_KEY</code> on the host. Jev modes will record an error until it is.
        </div>
      )}

      {isAdmin ? (
        <div className="settings-actions">
          <button
            className="btn-primary"
            disabled={saving || selected === savedEngine}
            onClick={handleSave}
          >
            {saving ? 'Saving...' : 'Save engine'}
          </button>
        </div>
      ) : (
        <p className="settings-card-sub assessment-engine-muted">
          Only an organisation administrator can change the assessment engine.
        </p>
      )}

      {statusError && (
        <p className="settings-card-sub assessment-engine-muted" role="alert">
          {statusError}
        </p>
      )}
      {status && (
        <>
          <p className="settings-card-sub assessment-engine-muted">
            Jev model <code>{status.jev_model_id}</code> · confidence cutoff{' '}
            {Math.round(status.confidence_cutoff * 100)}%
          </p>
          <ShadowStatsBlock stats={status.shadow_stats} cutoff={status.confidence_cutoff} />
        </>
      )}
    </div>
  )
}
