/**
 * CatalogUpgradePage — Platform → Catalog (plan §4.6).
 *
 * The platform-admin catalog upgrade wizard: version card, import history,
 * upload → staging progress → global diff preview with successor pairing →
 * typed-confirm apply → apply progress → completion report; revert with
 * blocker messaging.
 *
 * Access: the whole page is gated on is_platform_admin — App only routes here
 * for admins, and the page re-checks so a direct render shows nothing useful.
 */
import { useCallback, useEffect, useState } from 'react'
import { toast } from 'react-hot-toast'
import { useAuth } from '../../contexts/AuthContext'
import {
  applyCatalogUpgrade,
  cancelCatalogUpgradeRun,
  getCatalogStatusExtended,
  getCatalogUpgradeRun,
  listCatalogUpgradeRuns,
  revertCatalogUpgrade,
  uploadCatalogUpgrade,
} from '../../data/catalogUpgradeApi'
import type {
  CatalogStatusExtended,
  PlatformImportRunDetail,
  PlatformImportRunSummary,
} from '../../types/catalogUpgrade'
import VersionCard from './VersionCard'
import ImportRunHistory, { RunStatusBadge } from './ImportRunHistory'
import UploadStage from './UploadStage'
import DiffPreview from './DiffPreview'
import PairingEditor from './PairingEditor'
import ApplyConfirmDialog from './ApplyConfirmDialog'
import RevertDialog from './RevertDialog'
import CompletionReport from './CompletionReport'

/** Statuses during which the page polls the run detail. */
const IN_FLIGHT_STATUSES = ['staging', 'applying'] as const
const POLL_INTERVAL_MS = 2500

function isInFlight(status: string | undefined): boolean {
  return status !== undefined && (IN_FLIGHT_STATUSES as readonly string[]).includes(status)
}

function CatalogUpgradeConsole() {
  const [status, setStatus] = useState<CatalogStatusExtended | null>(null)
  const [runs, setRuns] = useState<PlatformImportRunSummary[]>([])
  const [runsTotal, setRunsTotal] = useState(0)
  const [loading, setLoading] = useState(true)
  const [activeRun, setActiveRun] = useState<PlatformImportRunDetail | null>(null)
  const [showApplyDialog, setShowApplyDialog] = useState(false)
  const [applying, setApplying] = useState(false)
  const [showRevertDialog, setShowRevertDialog] = useState(false)

  const loadOverview = useCallback(async () => {
    try {
      const [statusResponse, runsResponse] = await Promise.all([
        getCatalogStatusExtended(),
        listCatalogUpgradeRuns(),
      ])
      setStatus(statusResponse)
      setRuns(runsResponse.runs)
      setRunsTotal(runsResponse.total)
    } catch (err: unknown) {
      toast.error(err instanceof Error ? err.message : 'Failed to load catalog status')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    loadOverview()
  }, [loadOverview])

  const selectRun = useCallback(async (runId: string) => {
    try {
      const detail = await getCatalogUpgradeRun(runId)
      setActiveRun(detail)
    } catch (err: unknown) {
      toast.error(err instanceof Error ? err.message : 'Failed to load run')
    }
  }, [])

  // Poll the active run while staging/applying; refresh the overview when the
  // run settles so history and version card pick up the outcome.
  useEffect(() => {
    if (!activeRun || !isInFlight(activeRun.status)) return
    const runId = activeRun.id
    const timer = setInterval(async () => {
      try {
        const detail = await getCatalogUpgradeRun(runId)
        setActiveRun(current => (current?.id === runId ? detail : current))
        if (!isInFlight(detail.status)) {
          loadOverview()
        }
      } catch {
        // transient poll failure — keep polling
      }
    }, POLL_INTERVAL_MS)
    return () => clearInterval(timer)
  }, [activeRun?.id, activeRun?.status, loadOverview])

  const handleUpload = async (file: File) => {
    try {
      const response = await uploadCatalogUpgrade(file)
      toast.success('Workbook uploaded — staging started')
      await selectRun(response.run_id)
      await loadOverview()
    } catch (err: unknown) {
      toast.error(err instanceof Error ? err.message : 'Upload failed')
    }
  }

  const handleApply = async (confirmText: string) => {
    if (!activeRun?.to_version) return
    setApplying(true)
    try {
      await applyCatalogUpgrade(activeRun.id, activeRun.to_version, confirmText)
      setShowApplyDialog(false)
      toast.success(`Applying catalog ${activeRun.to_version}…`)
      await selectRun(activeRun.id)
    } catch (err: unknown) {
      toast.error(err instanceof Error ? err.message : 'Apply failed')
    } finally {
      setApplying(false)
    }
  }

  const handleCancel = async () => {
    if (!activeRun) return
    try {
      await cancelCatalogUpgradeRun(activeRun.id)
      toast.success('Run cancelled')
      await selectRun(activeRun.id)
      await loadOverview()
    } catch (err: unknown) {
      toast.error(err instanceof Error ? err.message : 'Cancel failed')
    }
  }

  // RevertDialog surfaces RevertBlockedError (409 + blocking orgs) itself.
  const handleRevert = async () => {
    if (!activeRun) return
    await revertCatalogUpgrade(activeRun.id)
    setShowRevertDialog(false)
    toast.success('Revert started')
    await selectRun(activeRun.id)
    await loadOverview()
  }

  const anyRunInFlight = runs.some(run => isInFlight(run.status)) || isInFlight(activeRun?.status)

  return (
    <div>
      <h2 style={{ marginBottom: '1rem' }}>Platform Catalog</h2>
      <VersionCard status={status} loading={loading} />
      <UploadStage disabled={anyRunInFlight} onUpload={handleUpload} />

      {activeRun && (
        <div className="surface-bench" style={{ padding: '1.25rem 1.5rem', marginTop: '1.5rem' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem', flexWrap: 'wrap', marginBottom: '0.75rem' }}>
            <h3 className="bench-header" style={{ margin: 0 }}>
              <span className="container-title">
                Run {activeRun.from_version || 'unversioned'} → {activeRun.to_version || '?'}
              </span>
            </h3>
            <RunStatusBadge status={activeRun.status} />
            <button
              className="btn btn-secondary btn-sm"
              style={{ marginLeft: 'auto' }}
              onClick={() => setActiveRun(null)}
            >
              Close
            </button>
          </div>

          {activeRun.status === 'staging' && (
            <div style={{ textAlign: 'center', padding: '2rem' }}>
              <div className="loading-spinner" />
              <p style={{ color: 'var(--muted)', marginTop: '0.75rem' }}>
                Staging workbook — parsing sheets and computing the diff against the live catalog…
              </p>
            </div>
          )}

          {activeRun.status === 'blocked' && (
            <div>
              <p>
                Staging found problems with this workbook — the run is blocked and cannot be
                applied.
              </p>
              {activeRun.sanity_report && (
                <ul style={{ paddingLeft: '1.25rem' }}>
                  {activeRun.sanity_report.checks.map(check => (
                    <li key={check.check} style={{ marginBottom: '0.25rem' }}>
                      {check.passed ? (
                        <span className="badge badge-active">pass</span>
                      ) : (
                        <span className="badge badge-revoked">fail</span>
                      )}{' '}
                      <strong>{check.check}</strong>
                      {check.detail && <span style={{ color: 'var(--muted)' }}> — {check.detail}</span>}
                    </li>
                  ))}
                </ul>
              )}
              <button className="btn btn-secondary" onClick={handleCancel}>
                Discard run
              </button>
            </div>
          )}

          {activeRun.status === 'staged' && (
            <div>
              <p style={{ color: 'var(--muted)' }}>
                Review the diff and pair deprecated controls, then apply. Nothing changes until
                the apply is confirmed.
              </p>
              <DiffPreview runId={activeRun.id} diffSummary={activeRun.diff_summary} />
              <PairingEditor
                runId={activeRun.id}
                pairings={activeRun.superseded_pairings}
                onPairingsSaved={pairings =>
                  setActiveRun(current =>
                    current ? { ...current, superseded_pairings: pairings } : current
                  )
                }
              />
              <div style={{ display: 'flex', gap: '0.75rem', marginTop: '1.25rem' }}>
                <button className="btn btn-secondary" onClick={handleCancel}>
                  Cancel run
                </button>
                <button className="btn btn-primary" onClick={() => setShowApplyDialog(true)}>
                  Apply upgrade…
                </button>
              </div>
            </div>
          )}

          {activeRun.status === 'applying' && (
            <div style={{ textAlign: 'center', padding: '2rem' }}>
              <div className="loading-spinner" />
              <p style={{ color: 'var(--muted)', marginTop: '0.75rem' }}>
                Applying catalog {activeRun.to_version}…
              </p>
            </div>
          )}

          {activeRun.status === 'applied' && (
            <div>
              <CompletionReport run={activeRun} />
              <div style={{ marginTop: '1.25rem' }}>
                <button className="btn btn-danger" onClick={() => setShowRevertDialog(true)}>
                  Revert upgrade…
                </button>
              </div>
            </div>
          )}

          {activeRun.status === 'failed' && (
            <div role="alert">
              <p>
                <strong>Run failed.</strong>{' '}
                {activeRun.error || 'No error detail was recorded.'}
              </p>
            </div>
          )}

          {activeRun.status === 'cancelled' && (
            <p style={{ color: 'var(--muted)' }}>This run was cancelled. Nothing was changed.</p>
          )}

          {activeRun.status === 'reverted' && (
            <p style={{ color: 'var(--muted)' }}>
              This upgrade was reverted{activeRun.reverted_at && ` on ${new Date(activeRun.reverted_at).toLocaleString()}`}.
              The catalog is back on {activeRun.from_version || 'its previous state'}.
            </p>
          )}
        </div>
      )}

      <ImportRunHistory
        runs={runs}
        total={runsTotal}
        activeRunId={activeRun?.id}
        onSelect={selectRun}
      />

      {showApplyDialog && activeRun?.to_version && (
        <ApplyConfirmDialog
          toVersion={activeRun.to_version}
          applying={applying}
          onConfirm={handleApply}
          onClose={() => setShowApplyDialog(false)}
        />
      )}

      {showRevertDialog && activeRun?.to_version && (
        <RevertDialog
          toVersion={activeRun.to_version}
          onConfirm={handleRevert}
          onClose={() => setShowRevertDialog(false)}
        />
      )}
    </div>
  )
}

export default function CatalogUpgradePage() {
  const { isPlatformAdmin } = useAuth()

  if (!isPlatformAdmin) {
    return (
      <div className="surface-bench" style={{ padding: '2rem' }}>
        <h2>Access denied</h2>
        <p style={{ color: 'var(--muted)' }}>
          This page is only available to platform administrators.
        </p>
      </div>
    )
  }
  return <CatalogUpgradeConsole />
}
