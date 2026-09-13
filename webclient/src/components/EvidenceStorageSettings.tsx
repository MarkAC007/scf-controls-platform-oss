/**
 * EvidenceStorageSettings — Settings → Evidence storage (organisation admin).
 *
 * Where this organisation's evidence files are written. Unlike Integrations,
 * which is platform-wide and takes no organisation, this card is org-scoped and
 * must be given one: an evidence store belongs to a tenant (ISA D14).
 *
 * Conventions are IntegrationsSettings.tsx's, deliberately reused rather than
 * reinvented:
 *   - Write-only. The API never returns a stored secret, so a configured row
 *     shows the fixed mask and nothing else. The mask is a constant, never
 *     derived from a value.
 *   - One editor open at a time. A half-typed credential in a hidden panel is
 *     a trap, not a feature, so opening any panel closes the others.
 *   - The typed secret is dropped before anything can re-render it.
 *   - Side reads are best-effort: a failure listing this organisation's own
 *     configurations must not blank the summary of what is actually in force.
 *   - The SCF_SECRET_KEY banner. Without that key the API refuses to store a
 *     credential with a 409, and the operator has to fix it on the host.
 *
 * Two things this screen must never do, both binding decisions (ISA D36, D42):
 *   - Never render `is_bundled` as a control and never send it. It is what
 *     exempts a row from the address safety refusals; a checkbox bound to it is
 *     a request to point the backend at the operator's own network. Only the
 *     installer sets it.
 *   - Never link a configuration id that came from the effective read when it
 *     is a platform row: an organisation administrator gets a 404 on it, so the
 *     link would dead-end.
 */
import { useCallback, useEffect, useState } from 'react'
import type { ReactNode } from 'react'

import {
  activateEvidenceStorageConfig,
  createEvidenceStorageConfig,
  deleteEvidenceStorageConfig,
  errorDetailOf,
  getEffectiveEvidenceStorage,
  listEvidenceStorageConfigs,
  retireEvidenceStorageConfig,
  rotateEvidenceStorageSecret,
  testEvidenceStorageConfig,
  updateEvidenceStorageConfig,
} from '../data/evidenceStorageApi'
import type {
  EvidenceStorageApiError,
  EvidenceStorageConfig,
  EvidenceStorageConfigInput,
  EvidenceStorageEffective,
  EvidenceStorageTestResult,
  StorageProvider,
} from '../data/evidenceStorageApi'

/** Fixed-width stand-in for a stored secret. Never derived from the value. */
const MASK = '••••••••'

/**
 * What each provider choice means for the form. Mirrors the `PRESETS` table in
 * `backend/services/storage_config.py` — labels included, so the dropdown and
 * the API agree on what a provider is called.
 */
interface ProviderChoice {
  provider: StorageProvider
  label: string
  help: string
  /** The endpoint is a property of the provider, not something an operator
   *  supplies, so the field is not rendered. */
  endpointFixed: boolean
  /** What that fixed endpoint is, for the read-only line. Empty for AWS, whose
   *  endpoint boto3 derives from the region. */
  fixedEndpoint: string
  pathStyle: boolean
  defaultRegion: string
  /** False for AWS alone: an instance role or IRSA supplies credentials there. */
  requiresCredentials: boolean
}

export const PROVIDER_CHOICES: ProviderChoice[] = [
  {
    provider: 'minio',
    label: 'MinIO',
    help:
      'Your own MinIO deployment. The address must be https and must not resolve to a loopback, private or link-local address. The MinIO the installer bundles is set up by the operator, not here.',
    endpointFixed: false,
    fixedEndpoint: '',
    pathStyle: true,
    defaultRegion: 'eu-west-1',
    requiresCredentials: true,
  },
  {
    provider: 'aws_s3',
    label: 'Amazon S3',
    help:
      "Amazon's own endpoint, derived from the region — there is no address to supply. Leave the key pair blank to use the instance role or IRSA the backend already runs under.",
    endpointFixed: true,
    fixedEndpoint: '',
    pathStyle: false,
    defaultRegion: 'eu-west-1',
    requiresCredentials: false,
  },
  {
    provider: 'gcs',
    label: 'Google Cloud Storage',
    help:
      'Reached over the S3-compatible XML API at storage.googleapis.com with an HMAC key pair, created under Interoperability in the Cloud Storage settings. A service account JSON key will not work here.',
    endpointFixed: true,
    fixedEndpoint: 'https://storage.googleapis.com',
    pathStyle: true,
    defaultRegion: 'auto',
    requiresCredentials: true,
  },
  {
    provider: 's3_compatible',
    label: 'Other S3-compatible',
    help:
      'Any store that speaks the S3 API. The address must be https and must not resolve to a loopback, private or link-local address.',
    endpointFixed: false,
    fixedEndpoint: '',
    pathStyle: true,
    defaultRegion: 'eu-west-1',
    requiresCredentials: true,
  },
]

function choiceFor(provider: string): ProviderChoice {
  return PROVIDER_CHOICES.find((c) => c.provider === provider) ?? PROVIDER_CHOICES[3]
}

/**
 * Chip text, mapped off `source` rather than off `managed_by_operator` alone:
 * the operator-managed case has two shapes and they mean different things to
 * whoever has to go and change it. Same three-label shape as
 * `IntegrationsSettings.tsx:31-35`.
 */
const SOURCE_CHIP: Record<string, string> = {
  org: 'Managed in app',
  platform: 'Managed by operator (platform default)',
  legacy_env: 'Managed by operator (environment)',
}

const STATUS_LABEL: Record<string, string> = {
  draft: 'Draft',
  active: 'Active',
  retired: 'Retired',
}

const STEP_LABEL: Record<string, string> = {
  address: 'Address check',
  put: 'Write a test object',
  get: 'Read it back',
  delete: 'Delete it',
}

function messageOf(err: unknown, fallback: string): string {
  return err instanceof Error && err.message ? err.message : fallback
}

/** Human timestamp; falls back to the raw string if it will not parse. */
function formatWhen(iso: string | null): string {
  if (!iso) return '—'
  const parsed = new Date(iso)
  return Number.isNaN(parsed.getTime()) ? iso : parsed.toLocaleString()
}

/** What one probe step says, without anything the far end sent back. */
function stepDetail(step: { status_code: number | null; error_class: string | null }): string {
  if (step.error_class) return step.error_class
  if (step.status_code !== null && step.status_code !== undefined) return `HTTP ${step.status_code}`
  return '—'
}

/** The editable shape of a configuration. No secret is ever read back into it. */
interface DraftForm {
  provider: StorageProvider
  bucket: string
  region: string
  endpoint_url: string
  public_endpoint: string
  access_key_id: string
  secret_access_key: string
}

function blankDraft(provider: StorageProvider = 'aws_s3'): DraftForm {
  return {
    provider,
    bucket: '',
    region: '',
    endpoint_url: '',
    public_endpoint: '',
    access_key_id: '',
    secret_access_key: '',
  }
}

/** Seed the editor from a draft row. The secret is not read back — there is
 *  nothing to read back — so it starts empty and an empty field means "leave
 *  the stored one alone". */
function draftFrom(row: EvidenceStorageConfig): DraftForm {
  return {
    provider: (row.provider as StorageProvider) || 's3_compatible',
    bucket: row.bucket || '',
    region: row.region || '',
    endpoint_url: row.endpoint_url || '',
    public_endpoint: row.public_endpoint || '',
    access_key_id: row.access_key_id || '',
    secret_access_key: '',
  }
}

export interface EvidenceStorageSettingsProps {
  organizationId: string
  /**
   * Extension point for the Phase 6 migration panel — progress of the copy job
   * and, when files still reference the old configuration, the reason a
   * configuration cannot yet be deleted.
   *
   * Deliberately empty in Phase 5: the copy operation does not exist yet, and a
   * panel with nothing behind it would be a promise the platform cannot keep.
   * Phase 6 passes its panel in here; nothing else about this card has to move.
   */
  migrationPanel?: ReactNode
}

export default function EvidenceStorageSettings({
  organizationId,
  migrationPanel,
}: EvidenceStorageSettingsProps) {
  const [effective, setEffective] = useState<EvidenceStorageEffective | null>(null)
  const [configs, setConfigs] = useState<EvidenceStorageConfig[]>([])
  const [listUnavailable, setListUnavailable] = useState(false)

  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [readOnly, setReadOnly] = useState(false)

  // Set when the API tells us there is no encryption key. The list and the
  // effective read do not carry that flag, so the 409 is where it comes from.
  const [encryptionKeyMissing, setEncryptionKeyMissing] = useState(false)

  // One panel open at a time: the editor, a rotation, or a delete confirmation.
  const [editor, setEditor] = useState<{ configId: string | null; form: DraftForm } | null>(null)
  const [rotatingId, setRotatingId] = useState<string | null>(null)
  const [rotateSecret, setRotateSecret] = useState('')
  const [rotateKeyId, setRotateKeyId] = useState('')
  const [confirmingDelete, setConfirmingDelete] = useState<string | null>(null)

  const [busy, setBusy] = useState(false)
  const [panelError, setPanelError] = useState<string | null>(null)
  const [rowError, setRowError] = useState<{ configId: string; message: string } | null>(null)

  // Probe results, per configuration, for this page view. `passed` is what
  // gates Activate: the server re-probes anyway and refuses with 409, but an
  // Activate button that can only fail is a button that should be disabled.
  const [reports, setReports] = useState<Record<string, EvidenceStorageTestResult>>({})

  const refresh = useCallback(async () => {
    const [effectiveResult, listResult] = await Promise.allSettled([
      getEffectiveEvidenceStorage(organizationId),
      listEvidenceStorageConfigs(organizationId),
    ])

    if (effectiveResult.status === 'fulfilled') {
      setEffective(effectiveResult.value)
      setLoadError(null)
      setReadOnly(false)
    } else {
      const status = (effectiveResult.reason as EvidenceStorageApiError)?.status
      // 403 is a real answer, not a failure to load: this organisation's
      // evidence store is an administrator's to change. Say so, rather than
      // rendering an empty card.
      setReadOnly(status === 403)
      setLoadError(
        status === 403
          ? null
          : messageOf(effectiveResult.reason, 'Failed to read the evidence storage configuration')
      )
    }

    // Best-effort: this organisation may have no configurations of its own, and
    // a failure here must not take the summary down with it.
    if (listResult.status === 'fulfilled') {
      setConfigs(listResult.value.items)
      setListUnavailable(false)
    } else {
      setConfigs([])
      setListUnavailable((listResult.reason as EvidenceStorageApiError)?.status !== 403)
    }
  }, [organizationId])

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

  const closePanels = () => {
    setEditor(null)
    setRotatingId(null)
    setRotateSecret('')
    setRotateKeyId('')
    setConfirmingDelete(null)
    setPanelError(null)
  }

  /** Note a 409 that says the platform cannot encrypt anything at all. */
  const noteEncryptionKey = (err: unknown) => {
    const detail = errorDetailOf(err)
    if (detail && detail.encryption_key_configured === false) setEncryptionKeyMissing(true)
  }

  const openCreate = () => {
    closePanels()
    setEditor({ configId: null, form: blankDraft() })
  }

  const openEdit = (row: EvidenceStorageConfig) => {
    closePanels()
    setEditor({ configId: row.id, form: draftFrom(row) })
  }

  const openRotate = (configId: string) => {
    closePanels()
    setRotatingId(configId)
  }

  const openDelete = (configId: string) => {
    closePanels()
    setConfirmingDelete(configId)
  }

  /** Only the fields this provider actually uses are sent. */
  const payloadFrom = (form: DraftForm): EvidenceStorageConfigInput => {
    const choice = choiceFor(form.provider)
    const payload: EvidenceStorageConfigInput = {
      provider: form.provider,
      bucket: form.bucket.trim(),
      region: form.region.trim(),
      access_key_id: form.access_key_id.trim(),
    }
    if (!choice.endpointFixed) {
      payload.endpoint_url = form.endpoint_url.trim()
      payload.public_endpoint = form.public_endpoint.trim()
    }
    if (form.secret_access_key) payload.secret_access_key = form.secret_access_key
    return payload
  }

  const handleSaveDraft = async () => {
    if (!editor) return
    const form = editor.form
    if (!form.bucket.trim()) {
      setPanelError('Enter the bucket this organisation writes evidence to.')
      return
    }
    setBusy(true)
    setPanelError(null)
    try {
      const payload = payloadFrom(form)
      const saved = editor.configId
        ? await updateEvidenceStorageConfig(organizationId, editor.configId, payload)
        : await createEvidenceStorageConfig(organizationId, payload)
      // Drop the typed secret before anything else can render it.
      setEditor(null)
      // Any change invalidates a passing probe: what passed is not what is
      // saved any more.
      setReports((prev) => {
        const next = { ...prev }
        delete next[saved.id]
        return next
      })
      await refresh()
    } catch (err) {
      noteEncryptionKey(err)
      setPanelError(messageOf(err, 'Failed to save the storage configuration'))
    } finally {
      setBusy(false)
    }
  }

  const handleTest = async (configId: string) => {
    setBusy(true)
    setRowError(null)
    try {
      const result = await testEvidenceStorageConfig(organizationId, configId)
      setReports((prev) => ({ ...prev, [configId]: result }))
    } catch (err) {
      noteEncryptionKey(err)
      setRowError({ configId, message: messageOf(err, 'The connection test could not be run') })
    } finally {
      setBusy(false)
    }
  }

  const handleActivate = async (configId: string) => {
    setBusy(true)
    setRowError(null)
    try {
      await activateEvidenceStorageConfig(organizationId, configId)
      await refresh()
    } catch (err) {
      // A failed activation answers 409 with the per-step report: show the
      // steps, not just the sentence.
      const detail = errorDetailOf(err)
      const steps = detail?.report?.steps
      if (steps) {
        setReports((prev) => ({
          ...prev,
          [configId]: { success: false, config_id: configId, steps },
        }))
      } else {
        // No steps came back — an encryption-key 409, an address refusal, a
        // race with another administrator. Whatever it was, the activation did
        // NOT happen, so a previously passing probe must not be left sitting
        // there re-enabling the Activate button. Clearing it re-gates the
        // action behind a fresh Test (Phase 5 finding F2).
        setReports((prev) => {
          if (!(configId in prev)) return prev
          const next = { ...prev }
          delete next[configId]
          return next
        })
      }
      setRowError({ configId, message: messageOf(err, 'Failed to activate the configuration') })
    } finally {
      setBusy(false)
    }
  }

  const handleRotate = async (configId: string) => {
    if (!rotateSecret) {
      setPanelError('Enter the replacement secret.')
      return
    }
    setBusy(true)
    setPanelError(null)
    try {
      await rotateEvidenceStorageSecret(organizationId, configId, {
        secret_access_key: rotateSecret,
        access_key_id: rotateKeyId.trim() || undefined,
      })
      // Drop the typed secret before anything else can render it.
      setRotateSecret('')
      setRotateKeyId('')
      setRotatingId(null)
      // The credential this configuration was proved with is no longer the
      // credential it holds, so the passing probe no longer says anything
      // about it. Clearing it re-gates Activate behind a fresh Test, the same
      // way saving an edit does (Phase 5 finding F1). The server re-probes on
      // activate regardless; this is about not showing a green tick for a
      // credential nobody has tested.
      setReports((prev) => {
        if (!(configId in prev)) return prev
        const next = { ...prev }
        delete next[configId]
        return next
      })
      await refresh()
    } catch (err) {
      noteEncryptionKey(err)
      setPanelError(messageOf(err, 'Failed to rotate the credential'))
    } finally {
      setBusy(false)
    }
  }

  const handleRetire = async (configId: string) => {
    setBusy(true)
    setRowError(null)
    try {
      await retireEvidenceStorageConfig(organizationId, configId)
      await refresh()
    } catch (err) {
      setRowError({ configId, message: messageOf(err, 'Failed to retire the configuration') })
    } finally {
      setBusy(false)
    }
  }

  const handleDelete = async (configId: string) => {
    setBusy(true)
    setPanelError(null)
    try {
      await deleteEvidenceStorageConfig(organizationId, configId)
      setConfirmingDelete(null)
      await refresh()
    } catch (err) {
      const detail = errorDetailOf(err)
      const count = detail?.evidence_file_count
      setPanelError(
        typeof count === 'number'
          ? `${messageOf(err, 'This configuration is still in use')} (${count} evidence ${
              count === 1 ? 'file is' : 'files are'
            } still stored under it.)`
          : messageOf(err, 'Failed to delete the configuration')
      )
    } finally {
      setBusy(false)
    }
  }

  if (loading) {
    return (
      <div className="settings-card">
        <h2>Evidence storage</h2>
        <p className="settings-card-sub">Loading…</p>
      </div>
    )
  }

  if (readOnly) {
    return (
      <div className="settings-card evidence-storage-settings">
        <h2>Evidence storage</h2>
        <p className="settings-card-sub">
          Where this organisation&rsquo;s evidence files are written.
        </p>
        <div className="integration-banner" data-testid="evidence-storage-forbidden" role="status">
          <strong>You cannot change this.</strong> Evidence storage is an organisation
          administrator&rsquo;s to configure. Ask an administrator of this organisation if the
          store needs to change.
        </div>
      </div>
    )
  }

  if (loadError) {
    return (
      <div className="settings-card evidence-storage-settings">
        <h2>Evidence storage</h2>
        <p className="integration-row-error" role="alert">
          {loadError}
        </p>
      </div>
    )
  }

  const source = effective?.source ?? 'legacy_env'
  const configured = Boolean(effective?.configured)
  const operatorManaged = Boolean(effective?.managed_by_operator)
  // Read, not inferred (D48). This used to be `source === 'platform' &&
  // provider === 'minio'`, which was true only while the installer stayed the
  // sole writer of platform rows — a property nobody should have to keep true,
  // and one an organisation administrator cannot check because they cannot
  // read the platform row. The effective response now carries the row's own
  // column.
  const isBundled = Boolean(effective?.is_bundled)
  const headline = isBundled
    ? 'MinIO — bundled, installed by the installer'
    : effective?.provider_label || 'Not configured'

  return (
    <div className="settings-card evidence-storage-settings">
      <h2>Evidence storage</h2>
      <p className="settings-card-sub">
        Where this organisation&rsquo;s evidence files are written. Credentials are stored
        encrypted and are never shown again after you save them.
      </p>

      {encryptionKeyMissing && (
        <div
          className="integration-banner integration-banner-warning"
          data-testid="evidence-storage-encryption-banner"
          role="alert"
        >
          <strong>SCF_SECRET_KEY is not configured.</strong> Without it the platform cannot
          encrypt credentials, so saving one here is refused. A store supplied by the operator
          through a secrets file or an environment variable still works &mdash; only in-app
          storage is unavailable. Run the installer, or set SCF_SECRET_KEY on the host, then
          reload this page.
        </div>
      )}

      <section className="evidence-storage-current" data-testid="evidence-storage-current">
        <h3>In force now</h3>
        {!configured ? (
          <>
            <p className="evidence-storage-headline" data-testid="evidence-storage-unconfigured">
              No evidence store is configured
            </p>
            <p className="integration-health-lead">
              Nothing has been configured for this organisation, for the platform, or in the
              environment. Evidence uploads will be refused until a store is configured here.
            </p>
          </>
        ) : (
          <>
            <p className="evidence-storage-headline">{headline}</p>
            <div className="integration-row-status">
              <span className="chip" data-testid="evidence-storage-source-chip">
                {SOURCE_CHIP[source] ?? source}
              </span>
              {effective?.key_version ? (
                <span className="chip">Key version {effective.key_version}</span>
              ) : null}
            </div>
            {/* Gated on `configured`, never on the provider: an unconfigured
                install still reports a provider and an SSE mode from the preset
                defaults, and rendering those as an address would be a lie. */}
            <dl className="evidence-storage-facts" data-testid="evidence-storage-address">
              <div>
                <dt>Bucket</dt>
                <dd>{effective?.bucket || '—'}</dd>
              </div>
              <div>
                <dt>Endpoint</dt>
                <dd>{effective?.endpoint_url || "The provider's own endpoint"}</dd>
              </div>
              <div>
                <dt>Region</dt>
                <dd>{effective?.region || '—'}</dd>
              </div>
              <div>
                <dt>Addressing</dt>
                <dd>{effective?.path_style ? 'Path-style' : 'Virtual-host'}</dd>
              </div>
              <div>
                <dt>Server-side encryption</dt>
                <dd>{effective?.sse_mode || 'none'}</dd>
              </div>
            </dl>
          </>
        )}

        {operatorManaged && configured && (
          <p className="integration-health-lead" data-testid="evidence-storage-operator-note">
            This store belongs to whoever runs the platform, so it cannot be edited here. You can
            still point this organisation at a store of its own.
          </p>
        )}

        <div className="integration-row-actions">
          <button
            type="button"
            className="btn btn-primary"
            data-testid="evidence-storage-use-different"
            disabled={busy}
            onClick={openCreate}
          >
            {configured && !operatorManaged ? 'Add another store' : 'Use a different store'}
          </button>
        </div>
      </section>

      {editor && (
        <section className="evidence-storage-editor" data-testid="evidence-storage-editor">
          <h3>{editor.configId ? 'Edit draft configuration' : 'New storage configuration'}</h3>

          <label htmlFor="evidence-storage-provider">Provider</label>
          <select
            id="evidence-storage-provider"
            data-testid="evidence-storage-provider"
            className="org-meta-input"
            value={editor.form.provider}
            disabled={busy}
            onChange={(e) =>
              setEditor({
                configId: editor.configId,
                form: { ...editor.form, provider: e.target.value as StorageProvider },
              })
            }
          >
            {PROVIDER_CHOICES.map((choice) => (
              <option key={choice.provider} value={choice.provider}>
                {choice.label}
              </option>
            ))}
          </select>
          <p className="integration-editor-hint" data-testid="evidence-storage-provider-help">
            {choiceFor(editor.form.provider).help}
          </p>

          <label htmlFor="evidence-storage-bucket">Bucket</label>
          <input
            id="evidence-storage-bucket"
            data-testid="evidence-storage-bucket"
            className="org-meta-input"
            autoComplete="off"
            spellCheck={false}
            value={editor.form.bucket}
            disabled={busy}
            onChange={(e) =>
              setEditor({ configId: editor.configId, form: { ...editor.form, bucket: e.target.value } })
            }
          />

          {choiceFor(editor.form.provider).endpointFixed ? (
            <p className="integration-editor-hint" data-testid="evidence-storage-endpoint-fixed">
              Endpoint:{' '}
              {choiceFor(editor.form.provider).fixedEndpoint ||
                "Amazon's own endpoint, derived from the region"}
            </p>
          ) : (
            <>
              <label htmlFor="evidence-storage-endpoint">Endpoint URL</label>
              <input
                id="evidence-storage-endpoint"
                data-testid="evidence-storage-endpoint"
                className="org-meta-input"
                autoComplete="off"
                spellCheck={false}
                placeholder="https://objects.example.com"
                value={editor.form.endpoint_url}
                disabled={busy}
                onChange={(e) =>
                  setEditor({
                    configId: editor.configId,
                    form: { ...editor.form, endpoint_url: e.target.value },
                  })
                }
              />

              <label htmlFor="evidence-storage-public-endpoint">
                Browser-facing address (optional)
              </label>
              <input
                id="evidence-storage-public-endpoint"
                data-testid="evidence-storage-public-endpoint"
                className="org-meta-input"
                autoComplete="off"
                spellCheck={false}
                placeholder="https://objects.example.com"
                value={editor.form.public_endpoint}
                disabled={busy}
                onChange={(e) =>
                  setEditor({
                    configId: editor.configId,
                    form: { ...editor.form, public_endpoint: e.target.value },
                  })
                }
              />
            </>
          )}

          <label htmlFor="evidence-storage-region">Region</label>
          <input
            id="evidence-storage-region"
            data-testid="evidence-storage-region"
            className="org-meta-input"
            autoComplete="off"
            spellCheck={false}
            placeholder={choiceFor(editor.form.provider).defaultRegion}
            value={editor.form.region}
            disabled={busy}
            onChange={(e) =>
              setEditor({ configId: editor.configId, form: { ...editor.form, region: e.target.value } })
            }
          />

          <label htmlFor="evidence-storage-access-key">
            Access key ID
            {choiceFor(editor.form.provider).requiresCredentials ? '' : ' (optional)'}
          </label>
          <input
            id="evidence-storage-access-key"
            data-testid="evidence-storage-access-key"
            className="org-meta-input"
            autoComplete="off"
            spellCheck={false}
            value={editor.form.access_key_id}
            disabled={busy}
            onChange={(e) =>
              setEditor({
                configId: editor.configId,
                form: { ...editor.form, access_key_id: e.target.value },
              })
            }
          />

          <label htmlFor="evidence-storage-secret">
            Secret access key
            {choiceFor(editor.form.provider).requiresCredentials ? '' : ' (optional)'}
          </label>
          <input
            id="evidence-storage-secret"
            data-testid="evidence-storage-secret"
            type="password"
            className="org-meta-input"
            autoComplete="off"
            spellCheck={false}
            placeholder={editor.configId ? 'Leave blank to keep the stored secret' : 'Paste the secret'}
            value={editor.form.secret_access_key}
            disabled={busy}
            onChange={(e) =>
              setEditor({
                configId: editor.configId,
                form: { ...editor.form, secret_access_key: e.target.value },
              })
            }
          />

          <div className="integration-editor-actions">
            <button
              type="button"
              className="btn btn-primary"
              data-testid="evidence-storage-save"
              disabled={busy}
              onClick={handleSaveDraft}
            >
              {busy ? 'Saving…' : 'Save draft'}
            </button>
            <button
              type="button"
              className="btn"
              data-testid="evidence-storage-cancel"
              disabled={busy}
              onClick={closePanels}
            >
              Cancel
            </button>
          </div>
          <p className="integration-editor-hint">
            A draft is inert: nothing is written to it until you test the connection and activate
            it. The secret is sent once and stored encrypted, and is never displayed again.
          </p>
          {panelError && (
            <p className="integration-row-error" role="alert">
              {panelError}
            </p>
          )}
        </section>
      )}

      <section className="evidence-storage-list-section">
        <h3>This organisation&rsquo;s configurations</h3>
        {listUnavailable && (
          <p className="integration-audit-empty">
            Your own configurations could not be listed right now. The store in force above is
            unaffected.
          </p>
        )}
        {!listUnavailable && configs.length === 0 && (
          <p className="integration-audit-empty" data-testid="evidence-storage-no-configs">
            This organisation has no storage configuration of its own.
          </p>
        )}
        {configs.length > 0 && (
          <ul className="integration-list">
            {configs.map((row) => {
              const report = reports[row.id]
              const passed = Boolean(report?.success)
              const isRotating = rotatingId === row.id
              const isDeleting = confirmingDelete === row.id
              return (
                <li
                  key={row.id}
                  className="integration-row"
                  data-testid={`evidence-storage-row-${row.id}`}
                >
                  <div className="integration-row-main">
                    <div className="integration-row-identity">
                      <span className="integration-label">{row.provider_label}</span>
                      <p className="integration-feature">
                        {row.bucket}
                        {row.endpoint_url ? ` · ${row.endpoint_url}` : ''}
                      </p>
                    </div>
                    <div className="integration-row-status">
                      <span
                        className={row.status === 'active' ? 'badge badge-success' : 'badge'}
                      >
                        {STATUS_LABEL[row.status] ?? row.status}
                      </span>
                      {row.secret_mask && (
                        <span className="integration-mask" aria-label="Secret hidden">
                          {MASK}
                        </span>
                      )}
                      <span className="chip">Key version {row.key_version}</span>
                    </div>
                    <div className="integration-row-actions">
                      <button
                        type="button"
                        className="btn"
                        data-testid={`evidence-storage-test-${row.id}`}
                        disabled={busy}
                        onClick={() => handleTest(row.id)}
                      >
                        Test connection
                      </button>
                      {row.status === 'draft' && (
                        <button
                          type="button"
                          className="btn btn-primary"
                          data-testid={`evidence-storage-activate-${row.id}`}
                          disabled={busy || !passed}
                          title={
                            passed
                              ? undefined
                              : 'Test the connection first. Activation re-runs the test and refuses if it fails.'
                          }
                          onClick={() => handleActivate(row.id)}
                        >
                          Activate
                        </button>
                      )}
                      {row.status === 'draft' && (
                        <button
                          type="button"
                          className="btn"
                          data-testid={`evidence-storage-edit-${row.id}`}
                          disabled={busy}
                          onClick={() => openEdit(row)}
                        >
                          Edit
                        </button>
                      )}
                      <button
                        type="button"
                        className="btn"
                        data-testid={`evidence-storage-rotate-${row.id}`}
                        disabled={busy}
                        onClick={() => openRotate(row.id)}
                      >
                        Rotate key
                      </button>
                      {row.status === 'active' && (
                        <button
                          type="button"
                          className="btn"
                          data-testid={`evidence-storage-retire-${row.id}`}
                          disabled={busy}
                          onClick={() => handleRetire(row.id)}
                        >
                          Retire
                        </button>
                      )}
                      <button
                        type="button"
                        className="btn btn-danger"
                        data-testid={`evidence-storage-delete-${row.id}`}
                        disabled={busy || row.status === 'active'}
                        title={
                          row.status === 'active'
                            ? 'Retire the configuration before deleting it.'
                            : undefined
                        }
                        onClick={() => openDelete(row.id)}
                      >
                        Delete
                      </button>
                    </div>
                  </div>

                  <p className="integration-row-meta">
                    Last changed {formatWhen(row.updated_at)}
                    {row.updated_by ? ` by ${row.updated_by}` : ''}
                  </p>

                  {report && (
                    <ul
                      className="evidence-storage-steps"
                      data-testid={`evidence-storage-report-${row.id}`}
                    >
                      {report.steps.map((step, index) => (
                        <li key={`${step.name}-${index}`}>
                          <span className="evidence-storage-step-name">
                            {STEP_LABEL[step.name] ?? step.name}
                          </span>
                          <span
                            className={
                              step.ok
                                ? 'evidence-storage-step-ok'
                                : 'evidence-storage-step-failed'
                            }
                          >
                            {step.ok ? 'Passed' : 'Failed'}
                          </span>
                          <span className="evidence-storage-step-detail">{stepDetail(step)}</span>
                        </li>
                      ))}
                    </ul>
                  )}

                  {isRotating && (
                    <div className="integration-editor">
                      <label htmlFor={`evidence-storage-rotate-secret-${row.id}`}>
                        New secret access key
                      </label>
                      <input
                        id={`evidence-storage-rotate-secret-${row.id}`}
                        data-testid={`evidence-storage-rotate-secret-${row.id}`}
                        type="password"
                        className="org-meta-input"
                        autoComplete="off"
                        spellCheck={false}
                        value={rotateSecret}
                        disabled={busy}
                        placeholder="Paste the replacement secret"
                        onChange={(e) => setRotateSecret(e.target.value)}
                      />
                      <label htmlFor={`evidence-storage-rotate-keyid-${row.id}`}>
                        New access key ID (optional)
                      </label>
                      <input
                        id={`evidence-storage-rotate-keyid-${row.id}`}
                        data-testid={`evidence-storage-rotate-keyid-${row.id}`}
                        className="org-meta-input"
                        autoComplete="off"
                        spellCheck={false}
                        value={rotateKeyId}
                        disabled={busy}
                        onChange={(e) => setRotateKeyId(e.target.value)}
                      />
                      <div className="integration-editor-actions">
                        <button
                          type="button"
                          className="btn btn-primary"
                          data-testid={`evidence-storage-rotate-save-${row.id}`}
                          disabled={busy}
                          onClick={() => handleRotate(row.id)}
                        >
                          {busy ? 'Rotating…' : 'Rotate'}
                        </button>
                        <button
                          type="button"
                          className="btn"
                          data-testid={`evidence-storage-rotate-cancel-${row.id}`}
                          disabled={busy}
                          onClick={closePanels}
                        >
                          Cancel
                        </button>
                      </div>
                      <p className="integration-editor-hint">
                        An active configuration is probed with the new credential before anything
                        is stored. A failing probe leaves the old credential in place.
                      </p>
                      {panelError && (
                        <p className="integration-row-error" role="alert">
                          {panelError}
                        </p>
                      )}
                    </div>
                  )}

                  {isDeleting && (
                    <div
                      className="integration-confirm"
                      role="alertdialog"
                      aria-label="Confirm delete"
                    >
                      <span>
                        Delete this configuration? Evidence already written under it must be
                        somewhere else first.
                      </span>
                      <button
                        type="button"
                        className="btn btn-danger"
                        data-testid={`evidence-storage-delete-confirm-${row.id}`}
                        disabled={busy}
                        onClick={() => handleDelete(row.id)}
                      >
                        {busy ? 'Deleting…' : 'Yes, delete it'}
                      </button>
                      <button
                        type="button"
                        className="btn"
                        data-testid={`evidence-storage-delete-cancel-${row.id}`}
                        disabled={busy}
                        onClick={closePanels}
                      >
                        Cancel
                      </button>
                      {panelError && (
                        <p className="integration-row-error" role="alert">
                          {panelError}
                        </p>
                      )}
                    </div>
                  )}

                  {rowError && rowError.configId === row.id && (
                    <p className="integration-row-error" role="alert">
                      {rowError.message}
                    </p>
                  )}
                </li>
              )
            })}
          </ul>
        )}
      </section>

      {/* ── Phase 6 extension point ───────────────────────────────────────────
          The migration panel — copy progress, and the reason a configuration
          cannot be deleted while files still reference it — mounts here. Phase
          5 renders nothing: the copy job does not exist yet. Switching provider
          does not move files that are already stored. */}
      {migrationPanel ?? null}
    </div>
  )
}
