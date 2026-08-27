/**
 * OrgReconciliationWizard — per-org preview → decide → apply → report flow
 * plus rollback (plan §4.3, §4.6), opened from the tenant reconciliation
 * board for one organisation.
 *
 * Preview is synchronous and creates the run; the section data (a–e) exists
 * only in the preview response, so a 'previewed' run reopened from the board
 * can only be discarded and re-previewed. Apply is gated on saved decisions:
 * every retire-only row justified and, on the org's first reconciliation,
 * the framework selections explicitly confirmed (plan §4.3e).
 */
import { useCallback, useEffect, useState } from 'react'
import { toast } from 'react-hot-toast'
import {
  applyOrgReconciliation,
  cancelOrgReconciliationRun,
  getOrgReconciliationRun,
  getOrgReconciliationStatus,
  listOrgReconciliationRuns,
  postOrgReconciliationPreview,
  putOrgReconciliationActions,
  rollbackOrgReconciliation,
} from '../../data/catalogUpgradeApi'
import type {
  OrgCatalogStatusResponse,
  OrgReconciliationRunDetail,
  PlannedAction,
  ReconciliationPreviewResponse,
} from '../../types/catalogUpgrade'
import {
  ChangedInScopeSection,
  FrameworkConfirmSection,
  OrphanReportSection,
  ScopeAdditionsSection,
} from './PreviewSections'
import ActionRadioTable from './ActionRadioTable'
import OrgApplyProgress from './OrgApplyProgress'
import OrgRollbackDialog from './OrgRollbackDialog'

const POLL_INTERVAL_MS = 2500

function isInFlight(status: string | undefined): boolean {
  return status === 'applying' || status === 'rolling_back'
}

/** Default decision for a deprecated impact: migrate when a successor is
 * paired, retain otherwise — mirroring the backend's suggested_action. */
function defaultActions(preview: ReconciliationPreviewResponse): Record<string, PlannedAction> {
  const actions: Record<string, PlannedAction> = {}
  for (const impact of preview.deprecated_impacts) {
    actions[impact.key] = impact.planned_action ?? {
      key: impact.key,
      entity: impact.entity,
      action: impact.suggested_action,
      justification: null,
      successor_scf_id: impact.suggested_action === 'migrate' ? impact.superseded_by ?? null : null,
    }
  }
  return actions
}

/** Rows the rollback will restore — the executed actions are the closest
 * client-visible proxy for the snapshot size; planned actions before apply. */
function rollbackRowCount(run: OrgReconciliationRunDetail): number {
  return run.actions_log.length > 0 ? run.actions_log.length : run.planned_actions.length
}

interface OrgReconciliationWizardProps {
  organizationId: string
  organizationName: string
  /** Called whenever a run settles (applied, failed, rolled back, cancelled)
   * so the board can refresh its rows. */
  onRunSettled: () => void
  onClose: () => void
}

export default function OrgReconciliationWizard({
  organizationId,
  organizationName,
  onRunSettled,
  onClose,
}: OrgReconciliationWizardProps) {
  const [orgStatus, setOrgStatus] = useState<OrgCatalogStatusResponse | null>(null)
  const [latestRun, setLatestRun] = useState<OrgReconciliationRunDetail | null>(null)
  const [loading, setLoading] = useState(true)
  const [preview, setPreview] = useState<ReconciliationPreviewResponse | null>(null)
  const [actions, setActions] = useState<Record<string, PlannedAction>>({})
  const [frameworkConfirmed, setFrameworkConfirmed] = useState(false)
  const [actionsSaved, setActionsSaved] = useState(false)
  const [saving, setSaving] = useState(false)
  const [previewing, setPreviewing] = useState(false)
  const [showRollbackDialog, setShowRollbackDialog] = useState(false)
  const [rollingBack, setRollingBack] = useState(false)

  // Load org status and the newest run (active or settled). The preview
  // sections themselves only exist for previews created in this session.
  const loadOrg = useCallback(async () => {
    try {
      const [status, runsResponse] = await Promise.all([
        getOrgReconciliationStatus(organizationId),
        listOrgReconciliationRuns(organizationId),
      ])
      setOrgStatus(status)
      const newestId = status.active_run?.id ?? runsResponse.runs[0]?.id
      if (newestId) {
        setLatestRun(await getOrgReconciliationRun(organizationId, newestId))
      } else {
        setLatestRun(null)
      }
    } catch (err: unknown) {
      toast.error(err instanceof Error ? err.message : 'Failed to load organisation status')
    } finally {
      setLoading(false)
    }
  }, [organizationId])

  useEffect(() => {
    setLoading(true)
    setPreview(null)
    setActions({})
    setFrameworkConfirmed(false)
    setActionsSaved(false)
    loadOrg()
  }, [loadOrg])

  // Poll while a run is applying or rolling back; refresh the board when it
  // settles so the row's eligibility and version update.
  useEffect(() => {
    if (!latestRun || !isInFlight(latestRun.status)) return
    const runId = latestRun.id
    const timer = setInterval(async () => {
      try {
        const detail = await getOrgReconciliationRun(organizationId, runId)
        setLatestRun(current => (current?.id === runId ? detail : current))
        if (!isInFlight(detail.status)) {
          onRunSettled()
          const status = await getOrgReconciliationStatus(organizationId)
          setOrgStatus(status)
        }
      } catch {
        // transient poll failure — keep polling
      }
    }, POLL_INTERVAL_MS)
    return () => clearInterval(timer)
  }, [organizationId, latestRun?.id, latestRun?.status, onRunSettled])

  const handlePreview = async () => {
    setPreviewing(true)
    try {
      const response = await postOrgReconciliationPreview(organizationId)
      setPreview(response)
      setActions(defaultActions(response))
      setFrameworkConfirmed(false)
      setActionsSaved(false)
      setLatestRun(await getOrgReconciliationRun(organizationId, response.run.id))
    } catch (err: unknown) {
      toast.error(err instanceof Error ? err.message : 'Preview failed')
    } finally {
      setPreviewing(false)
    }
  }

  const handleActionChange = (key: string, action: PlannedAction) => {
    setActions(current => ({ ...current, [key]: action }))
    setActionsSaved(false)
  }

  const handleFrameworkConfirmedChange = (confirmed: boolean) => {
    setFrameworkConfirmed(confirmed)
    setActionsSaved(false)
  }

  const justificationsValid = Object.values(actions).every(
    action => action.action !== 'retire_only' || Boolean(action.justification?.trim())
  )
  const frameworkConfirmRequired = preview?.framework_confirmation.required ?? false
  const canSave = justificationsValid && (!frameworkConfirmRequired || frameworkConfirmed)

  const handleSaveActions = async () => {
    if (!preview) return
    setSaving(true)
    try {
      await putOrgReconciliationActions(
        organizationId,
        preview.run.id,
        Object.values(actions),
        frameworkConfirmRequired
          ? preview.framework_confirmation.selections
              .filter(selection => selection.active)
              .map(selection => selection.framework_id)
          : undefined
      )
      setActionsSaved(true)
      toast.success('Decisions saved')
    } catch (err: unknown) {
      toast.error(err instanceof Error ? err.message : 'Failed to save decisions')
    } finally {
      setSaving(false)
    }
  }

  const handleApply = async () => {
    if (!preview?.run.to_version) return
    try {
      await applyOrgReconciliation(organizationId, preview.run.id, preview.run.to_version)
      toast.success(`Applying reconciliation to ${preview.run.to_version}…`)
      setLatestRun(await getOrgReconciliationRun(organizationId, preview.run.id))
      setPreview(null)
    } catch (err: unknown) {
      toast.error(err instanceof Error ? err.message : 'Apply failed')
    }
  }

  const handleDiscardPreview = async (runId: string) => {
    try {
      await cancelOrgReconciliationRun(organizationId, runId)
      toast.success('Preview discarded')
      setPreview(null)
      onRunSettled()
      await loadOrg()
    } catch (err: unknown) {
      toast.error(err instanceof Error ? err.message : 'Failed to discard the preview')
    }
  }

  const handleRollback = async (confirmText: string) => {
    if (!latestRun) return
    setRollingBack(true)
    try {
      await rollbackOrgReconciliation(organizationId, latestRun.id, confirmText)
      setShowRollbackDialog(false)
      toast.success('Rollback started')
      setLatestRun(await getOrgReconciliationRun(organizationId, latestRun.id))
    } catch (err: unknown) {
      toast.error(err instanceof Error ? err.message : 'Rollback failed')
    } finally {
      setRollingBack(false)
    }
  }

  const renderBody = () => {
    if (loading) {
      return (
        <div style={{ textAlign: 'center', padding: '2rem' }}>
          <div className="loading-spinner" />
        </div>
      )
    }

    if (latestRun && isInFlight(latestRun.status)) {
      return <OrgApplyProgress status={latestRun.status} toVersion={latestRun.to_version} />
    }

    // In-session preview: the full section a–e review and decision surface.
    if (preview && latestRun?.status === 'previewed') {
      return (
        <div>
          <p style={{ color: 'var(--muted)' }}>
            Reconciliation preview {preview.run.from_version || 'unversioned'} →{' '}
            {preview.run.to_version}. Nothing changes until the apply is confirmed.
          </p>
          <ScopeAdditionsSection additions={preview.additions} />
          <section style={{ marginBottom: '1.25rem' }}>
            <h4 style={{ marginBottom: '0.5rem' }}>Deprecated controls with organisation data</h4>
            <ActionRadioTable
              impacts={preview.deprecated_impacts}
              actions={actions}
              disabled={saving}
              onChange={handleActionChange}
            />
          </section>
          <ChangedInScopeSection changed={preview.changed_in_scope} />
          <OrphanReportSection orphans={preview.orphans} />
          <FrameworkConfirmSection
            confirmation={preview.framework_confirmation}
            confirmed={frameworkConfirmed}
            disabled={saving}
            onConfirmedChange={handleFrameworkConfirmedChange}
          />
          <div style={{ display: 'flex', gap: '0.75rem', marginTop: '1.25rem' }}>
            <button
              className="btn btn-secondary"
              onClick={() => handleDiscardPreview(preview.run.id)}
            >
              Discard preview
            </button>
            <button
              className="btn btn-secondary"
              disabled={!canSave || saving}
              onClick={handleSaveActions}
            >
              {saving ? 'Saving…' : 'Save decisions'}
            </button>
            <button className="btn btn-primary" disabled={!actionsSaved} onClick={handleApply}>
              Apply reconciliation
            </button>
          </div>
        </div>
      )
    }

    // A previewed run from an earlier session — the section data is gone.
    if (latestRun?.status === 'previewed') {
      return (
        <div>
          <p>
            A reconciliation preview to {latestRun.to_version} already exists for this
            organisation, but its review details are only available in the session that created
            it. Discard it to start a fresh preview.
          </p>
          <button
            className="btn btn-secondary"
            onClick={() => handleDiscardPreview(latestRun.id)}
          >
            Discard preview
          </button>
        </div>
      )
    }

    if (latestRun?.status === 'applied') {
      return (
        <div>
          <p>
            <strong>Reconciliation applied.</strong> {organizationName} moved from catalog{' '}
            {latestRun.from_version || 'unversioned'} to {latestRun.to_version}
            {latestRun.applied_at && ` on ${new Date(latestRun.applied_at).toLocaleString()}`}.
          </p>
          <p style={{ color: 'var(--muted)', fontSize: '0.85rem' }}>
            {latestRun.actions_log.length} action{latestRun.actions_log.length === 1 ? '' : 's'}{' '}
            executed. Organisation admins have been notified.
          </p>
          <button className="btn btn-danger" onClick={() => setShowRollbackDialog(true)}>
            Roll back…
          </button>
        </div>
      )
    }

    if (latestRun?.status === 'failed') {
      return (
        <div role="alert">
          <p>
            <strong>Run failed.</strong> {latestRun.error || 'No error detail was recorded.'}
          </p>
        </div>
      )
    }

    if (latestRun?.status === 'rolled_back') {
      return (
        <p style={{ color: 'var(--muted)' }}>
          The reconciliation to {latestRun.to_version} was rolled back
          {latestRun.rolled_back_at &&
            ` on ${new Date(latestRun.rolled_back_at).toLocaleString()}`}
          . The organisation is back on {latestRun.from_version || 'its previous version'}.
        </p>
      )
    }

    if (orgStatus?.eligible) {
      return (
        <div>
          <p>
            Catalog <strong>{orgStatus.platform_catalog_version}</strong> is available —{' '}
            {organizationName} is reconciled to{' '}
            <strong>{orgStatus.reconciled_catalog_version || 'no recorded version'}</strong>.
          </p>
          <button className="btn btn-primary" disabled={previewing} onClick={handlePreview}>
            {previewing ? 'Building preview…' : 'Preview reconciliation'}
          </button>
        </div>
      )
    }

    return (
      <p style={{ color: 'var(--muted)' }}>
        {organizationName} is up to date with the platform catalog
        {orgStatus?.reconciled_catalog_version && ` (${orgStatus.reconciled_catalog_version})`}.
      </p>
    )
  }

  return (
    <div className="surface-bench" style={{ padding: '1.25rem 1.5rem', marginTop: '1.5rem' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem', flexWrap: 'wrap', marginBottom: '0.75rem' }}>
        <h3 className="bench-header" style={{ margin: 0 }}>
          <span className="container-title">{organizationName}</span>
        </h3>
        {latestRun?.from_version && latestRun.to_version && (
          <span className="platform-version-range">
            — reconcile {latestRun.from_version} → {latestRun.to_version}
          </span>
        )}
        <button
          className="btn btn-secondary btn-sm"
          style={{ marginLeft: 'auto' }}
          onClick={onClose}
        >
          Close
        </button>
      </div>
      {renderBody()}

      {showRollbackDialog && latestRun?.to_version && (
        <OrgRollbackDialog
          organizationName={organizationName}
          toVersion={latestRun.to_version}
          fromVersion={latestRun.from_version}
          rowCount={rollbackRowCount(latestRun)}
          rollingBack={rollingBack}
          onConfirm={handleRollback}
          onClose={() => setShowRollbackDialog(false)}
        />
      )}
    </div>
  )
}
