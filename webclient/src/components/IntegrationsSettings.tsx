/**
 * IntegrationsSettings — Settings → Integrations (platform admin only).
 *
 * One row per tier-3 credential (issue #947, CONTRACT.md §4). The screen is
 * write-only: the API never returns a stored value, so a configured row shows a
 * fixed mask and nothing else. Values supplied by the operator through a file or
 * an environment variable are shown but locked — the app must not overwrite what
 * the host owns.
 */
import { useCallback, useEffect, useState } from 'react'

import {
  listIntegrations,
  setIntegration,
  clearIntegration,
  getIntegrationsHealth,
  getIntegrationsAudit,
} from '../data/integrationsApi'
import type {
  IntegrationAuditEntry,
  IntegrationItem,
  IntegrationsHealthResponse,
} from '../data/integrationsApi'

/** Fixed-width stand-in for a stored value. Never derived from the value. */
const MASK = '••••••••'

/** How many audit entries the "Recent changes" list asks for. */
const AUDIT_LIMIT = 10

const SOURCE_CHIP: Record<'db' | 'file' | 'env', string> = {
  db: 'Managed in app',
  file: 'Managed by operator (file)',
  env: 'Managed by operator (env)',
}

const AUDIT_ACTION_LABEL: Record<string, string> = {
  'integration.secret.set': 'Set',
  'integration.secret.replaced': 'Replaced',
  'integration.secret.cleared': 'Cleared',
}

const OPERATOR_MANAGED_HINT =
  'Set by the operator in a secrets file or environment variable. Change it on the host, not here.'

function messageOf(err: unknown, fallback: string): string {
  return err instanceof Error && err.message ? err.message : fallback
}

/** Human timestamp; falls back to the raw string if it will not parse. */
function formatWhen(iso: string | null): string {
  if (!iso) return '—'
  const parsed = new Date(iso)
  return Number.isNaN(parsed.getTime()) ? iso : parsed.toLocaleString()
}

export default function IntegrationsSettings() {
  const [items, setItems] = useState<IntegrationItem[]>([])
  const [encryptionKeyConfigured, setEncryptionKeyConfigured] = useState(true)
  const [legacyPlaintextRows, setLegacyPlaintextRows] = useState(0)
  const [health, setHealth] = useState<IntegrationsHealthResponse | null>(null)
  const [audit, setAudit] = useState<IntegrationAuditEntry[] | null>(null)
  const [auditFailed, setAuditFailed] = useState(false)

  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState<string | null>(null)

  // Per-row editing state. Only one row is ever open at a time — a half-typed
  // credential in a hidden row is a trap, not a feature.
  const [editingName, setEditingName] = useState<string | null>(null)
  const [draftValue, setDraftValue] = useState('')
  const [confirmingClear, setConfirmingClear] = useState<string | null>(null)
  const [rowError, setRowError] = useState<string | null>(null)
  const [busyName, setBusyName] = useState<string | null>(null)

  /**
   * Re-read the inventory. Health and audit are best-effort: they are extra
   * views over the same data, so a failure there must not blank the list the
   * operator actually came here to use.
   */
  const refresh = useCallback(async () => {
    const [listResult, healthResult, auditResult] = await Promise.allSettled([
      listIntegrations(),
      getIntegrationsHealth(),
      getIntegrationsAudit(AUDIT_LIMIT),
    ])

    if (listResult.status === 'fulfilled') {
      setItems(listResult.value.items)
      setEncryptionKeyConfigured(listResult.value.encryption_key_configured)
      setLegacyPlaintextRows(listResult.value.legacy_plaintext_rows)
      setLoadError(null)
    } else {
      setLoadError(messageOf(listResult.reason, 'Failed to load integrations'))
    }

    setHealth(healthResult.status === 'fulfilled' ? healthResult.value : null)

    if (auditResult.status === 'fulfilled') {
      setAudit(auditResult.value.items)
      setAuditFailed(false)
    } else {
      setAudit(null)
      setAuditFailed(true)
    }
  }, [])

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    refresh().finally(() => {
      if (!cancelled) setLoading(false)
    })
    return () => {
      cancelled = true
    }
  }, [refresh])

  const openEditor = (name: string) => {
    setEditingName(name)
    setDraftValue('')
    setRowError(null)
    setConfirmingClear(null)
  }

  const closeEditor = () => {
    setEditingName(null)
    setDraftValue('')
    setRowError(null)
  }

  const handleSave = async (name: string) => {
    const value = draftValue.trim()
    if (!value) {
      setRowError('Enter a value before saving.')
      return
    }
    setBusyName(name)
    setRowError(null)
    try {
      await setIntegration(name, value)
      // Drop the typed value before anything else can render it.
      setDraftValue('')
      setEditingName(null)
      await refresh()
    } catch (err) {
      // 409 (no encryption key / operator-managed) and 422 (empty) both carry a
      // detail message worth showing verbatim; the editor stays open so the
      // operator can fix the cause and retry.
      setRowError(messageOf(err, 'Failed to save the credential'))
    } finally {
      setBusyName(null)
    }
  }

  const handleClear = async (name: string) => {
    setBusyName(name)
    setRowError(null)
    try {
      await clearIntegration(name)
      setConfirmingClear(null)
      await refresh()
    } catch (err) {
      setRowError(messageOf(err, 'Failed to clear the credential'))
    } finally {
      setBusyName(null)
    }
  }

  const byName = new Map(items.map((i) => [i.name, i]))
  const pick = (names: string[]) =>
    names.map((n) => byName.get(n)).filter((i): i is IntegrationItem => Boolean(i))

  const configuredItems = health
    ? pick(health.configured)
    : items.filter((i) => i.configured)
  const unconfiguredItems = health
    ? pick(health.unconfigured)
    : items.filter((i) => !i.configured)

  if (loading) {
    return (
      <div className="settings-card">
        <h2>Integrations</h2>
        <p className="settings-card-sub">Loading…</p>
      </div>
    )
  }

  if (loadError) {
    return (
      <div className="settings-card">
        <h2>Integrations</h2>
        <p className="integration-row-error" role="alert">
          {loadError}
        </p>
      </div>
    )
  }

  return (
    <div className="settings-card integrations-settings">
      <h2>Integrations</h2>
      <p className="settings-card-sub">
        Credentials for the optional services this platform can talk to. Values are
        stored encrypted and are never shown again after you save them.
      </p>

      {!encryptionKeyConfigured && (
        <div
          className="integration-banner integration-banner-warning"
          data-testid="integration-encryption-banner"
          role="alert"
        >
          <strong>SCF_SECRET_KEY is not configured.</strong>{' '}
          Without it the platform cannot encrypt credentials, so saving one here is
          refused. Credentials supplied by the operator through a secrets file or an
          environment variable still work — only in-app storage is unavailable. Run
          the installer, or set SCF_SECRET_KEY on the host, then reload this page.
        </div>
      )}

      {legacyPlaintextRows > 0 && (
        <div className="integration-banner" data-testid="integration-legacy-banner">
          {legacyPlaintextRows} stored credential
          {legacyPlaintextRows === 1 ? '' : 's'} predate encryption. Run{' '}
          <code>scf-admin backfill-encrypt</code> on the host to encrypt them.
        </div>
      )}

      <ul className="integration-list">
        {items.map((row) => {
          const isEditing = editingName === row.name
          const isConfirming = confirmingClear === row.name
          const isBusy = busyName === row.name
          const canClear = row.source === 'db'
          return (
            <li
              key={row.name}
              className="integration-row"
              data-testid={`integration-row-${row.name}`}
            >
              <div className="integration-row-main">
                <div className="integration-row-identity">
                  <span className="integration-label">{row.label}</span>
                  <p className="integration-feature">{row.feature}</p>
                </div>
                <div className="integration-row-status">
                  <span className={row.configured ? 'badge badge-success' : 'badge'}>
                    {row.configured ? 'Configured' : 'Not configured'}
                  </span>
                  {row.source && <span className="chip">{SOURCE_CHIP[row.source]}</span>}
                  {row.configured && (
                    <span className="integration-mask" aria-label="Value hidden">
                      {MASK}
                    </span>
                  )}
                </div>
                <div className="integration-row-actions">
                  <button
                    type="button"
                    className="btn"
                    data-testid={`integration-replace-${row.name}`}
                    disabled={row.managed_by_operator || isBusy}
                    title={row.managed_by_operator ? OPERATOR_MANAGED_HINT : undefined}
                    onClick={() => (isEditing ? closeEditor() : openEditor(row.name))}
                  >
                    {row.configured ? 'Replace' : 'Set value'}
                  </button>
                  <button
                    type="button"
                    className="btn btn-danger"
                    data-testid={`integration-clear-${row.name}`}
                    disabled={!canClear || isBusy}
                    title={
                      canClear
                        ? undefined
                        : 'No value is stored in the app for this credential.'
                    }
                    onClick={() => {
                      // Drop any half-typed credential in an open editor rather
                      // than leaving it live behind a confirm prompt.
                      setEditingName(null)
                      setDraftValue('')
                      setConfirmingClear(row.name)
                      setRowError(null)
                    }}
                  >
                    Clear
                  </button>
                </div>
              </div>

              {row.configured && row.source === 'db' && (
                <p className="integration-row-meta">
                  Last changed {formatWhen(row.updated_at)}
                  {row.updated_by ? ` by ${row.updated_by}` : ''}
                </p>
              )}

              {isEditing && (
                <div className="integration-editor">
                  <label htmlFor={`integration-input-${row.name}`}>New value</label>
                  <input
                    id={`integration-input-${row.name}`}
                    data-testid={`integration-value-${row.name}`}
                    type="password"
                    className="org-meta-input"
                    autoComplete="off"
                    spellCheck={false}
                    value={draftValue}
                    disabled={isBusy}
                    placeholder="Paste the credential"
                    onChange={(e) => setDraftValue(e.target.value)}
                  />
                  <div className="integration-editor-actions">
                    <button
                      type="button"
                      className="btn btn-primary"
                      data-testid={`integration-save-${row.name}`}
                      disabled={isBusy}
                      onClick={() => handleSave(row.name)}
                    >
                      {isBusy ? 'Saving…' : 'Save'}
                    </button>
                    <button
                      type="button"
                      className="btn"
                      data-testid={`integration-cancel-${row.name}`}
                      disabled={isBusy}
                      onClick={closeEditor}
                    >
                      Cancel
                    </button>
                  </div>
                  <p className="integration-editor-hint">
                    The value is sent once and stored encrypted. It is never displayed
                    again.
                  </p>
                </div>
              )}

              {isConfirming && (
                <div className="integration-confirm" role="alertdialog" aria-label="Confirm clear">
                  <span>
                    Remove the stored {row.label}? Anything using it stops working until
                    a new value is saved.
                  </span>
                  <button
                    type="button"
                    className="btn btn-danger"
                    data-testid={`integration-clear-confirm-${row.name}`}
                    disabled={isBusy}
                    onClick={() => handleClear(row.name)}
                  >
                    {isBusy ? 'Clearing…' : 'Yes, clear it'}
                  </button>
                  <button
                    type="button"
                    className="btn"
                    data-testid={`integration-clear-cancel-${row.name}`}
                    disabled={isBusy}
                    onClick={() => setConfirmingClear(null)}
                  >
                    Cancel
                  </button>
                </div>
              )}

              {rowError && (isEditing || isConfirming) && (
                <p className="integration-row-error" role="alert">
                  {rowError}
                </p>
              )}
            </li>
          )
        })}
      </ul>

      <section className="integration-health" data-testid="integration-health-panel">
        <h3>Setup health</h3>
        <p className="integration-health-count">
          {configuredItems.length} of {items.length} configured
          {health ? ` · operator values supplied by ${health.secrets_dir_mode}` : ''}
        </p>
        {unconfiguredItems.length > 0 ? (
          <>
            <p className="integration-health-lead">Still to configure, and what each unlocks:</p>
            <ul className="integration-health-list">
              {unconfiguredItems.map((row) => (
                <li key={row.name}>
                  <span className="integration-health-name">{row.label}</span>
                  <span className="integration-health-feature">{row.feature}</span>
                </li>
              ))}
            </ul>
          </>
        ) : (
          <p className="integration-health-lead">
            Every integration credential is configured.
          </p>
        )}
      </section>

      <section className="integration-audit">
        <h3>Recent changes</h3>
        {auditFailed && (
          <p className="integration-audit-empty">
            Recent changes are unavailable right now. The credentials above are
            unaffected.
          </p>
        )}
        {!auditFailed && audit && audit.length === 0 && (
          <p className="integration-audit-empty">No credential changes recorded yet.</p>
        )}
        {!auditFailed && audit && audit.length > 0 && (
          <ul className="integration-audit-list" data-testid="integration-audit-list">
            {audit.map((entry, index) => (
              <li key={`${entry.created_at}-${entry.entity_id}-${index}`}>
                <span className="integration-audit-action">
                  {AUDIT_ACTION_LABEL[entry.action] ?? entry.action}
                </span>
                <span className="integration-audit-name">
                  {byName.get(entry.entity_id)?.label ?? entry.entity_id}
                </span>
                <span className="integration-audit-actor">{entry.actor}</span>
                <span className="integration-audit-when">{formatWhen(entry.created_at)}</span>
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  )
}
