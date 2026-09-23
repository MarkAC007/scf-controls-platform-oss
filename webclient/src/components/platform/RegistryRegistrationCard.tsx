/**
 * RegistryRegistrationCard — recover the live catalog's framework registry
 * from the UI (multipart POST /api/admin/catalog/framework-registry).
 *
 * Shown inside a blocked run when the ``live_framework_registry`` sanity check
 * failed: the platform has no stored framework list (focal-document
 * identifiers) for the catalog version currently live, so the framework
 * churn gate cannot tell a renamed framework from a retired one. Registering
 * the live version's own workbook stores that list; the catalog is untouched.
 * The admin then discards the blocked run and re-uploads the new workbook.
 */
import { useState } from 'react'
import { registerFrameworkRegistry } from '../../data/catalogUpgradeApi'
import type { FrameworkRegistryRegistration } from '../../types/catalogUpgrade'

interface RegistryRegistrationCardProps {
  /** The run's from_version — the catalog version currently live. */
  liveVersion?: string | null
  /** The run's to_version — the workbook the admin will re-upload afterwards. */
  targetVersion?: string | null
  /** Refresh the version card's registry line after a successful registration. */
  onRegistered?: () => void
}

export default function RegistryRegistrationCard({
  liveVersion,
  targetVersion,
  onRegistered,
}: RegistryRegistrationCardProps) {
  const [file, setFile] = useState<File | null>(null)
  const [submitting, setSubmitting] = useState(false)
  const [result, setResult] = useState<FrameworkRegistryRegistration | null>(null)
  const [error, setError] = useState<string | null>(null)

  const liveLabel = liveVersion || 'the live catalog'
  const targetLabel = targetVersion || 'new'

  const handleRegister = async () => {
    if (!file) return
    setSubmitting(true)
    setError(null)
    try {
      const registration = await registerFrameworkRegistry(file)
      setResult(registration)
      setFile(null)
      onRegistered?.()
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Registration failed')
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div
      style={{
        padding: '1rem 1.25rem',
        margin: '1rem 0',
        background: 'var(--card)',
        border: '1px solid var(--border)',
        borderRadius: '10px',
      }}
    >
      <h4 style={{ margin: '0 0 0.5rem' }}>Register your current catalog workbook</h4>
      <p style={{ color: 'var(--muted)', marginBottom: '1rem' }}>
        The platform needs the SCF workbook for {liveLabel}, the catalog version currently
        live, so it can recognise which frameworks the new release merely renamed and which it
        actually retired. Registering the workbook stores that framework list and its
        focal-document identifiers. Nothing in your catalog changes.
      </p>

      <div style={{ display: 'flex', gap: '0.75rem', alignItems: 'center', flexWrap: 'wrap' }}>
        <input
          type="file"
          accept=".xlsx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
          aria-label="Current catalog workbook"
          disabled={submitting}
          onChange={e => {
            setFile(e.target.files?.[0] ?? null)
            setError(null)
          }}
        />
        <button
          className="btn btn-primary"
          disabled={!file || submitting}
          onClick={handleRegister}
        >
          {submitting ? 'Registering…' : 'Register workbook'}
        </button>
      </div>

      {result && (
        <div style={{ marginTop: '1rem' }}>
          <p style={{ margin: '0 0 0.35rem' }}>
            <span className="badge badge-active">registered</span>{' '}
            <span>
              Registered {result.catalog_version}: {result.entries} frameworks,{' '}
              {result.with_focal_document_id} with a focal-document identifier
            </span>
          </p>
          <p style={{ color: 'var(--muted)', margin: 0 }}>
            Next: <span>Discard this run and upload the {targetLabel} workbook again</span>
          </p>
        </div>
      )}

      {error && (
        <p role="alert" style={{ color: 'var(--danger)', marginTop: '1rem', marginBottom: 0 }}>
          {error}
        </p>
      )}
    </div>
  )
}
