/**
 * MaturityTargetsSettings — Settings → Maturity targets (PIG R5).
 *
 * The level each SCF domain's controls are meant to reach. A control inherits
 * its domain's target unless it carries its own override (set on the control's
 * GOVERNANCE tab). Readable by any member; the PATCH is admin-only, so the
 * selects render read-only below admin.
 *
 * The PATCH replaces the stored map rather than merging into it, so Save
 * re-reads the stored map and applies only the rows this admin changed, which
 * keeps another admin's concurrent edits; a stored code the active catalogue
 * no longer lists is kept as its own row instead of being dropped by omission.
 * For the same reason no editor is offered when the settings could not be
 * read: saving over a failed read would wipe every target.
 */
import { useEffect, useMemo, useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { toast } from 'react-hot-toast'

import { fetchOrganizationSettings, updateOrganizationSettings } from '../data/apiClient'
import { MATURITY_LEVEL_OPTIONS } from '../data/targetMaturity'
import { domainFilterLabel, useCatalogDomains } from '../hooks/useCatalogFilters'
import { useIsOrgAdmin } from '../hooks/useIsOrgAdmin'
import { useOrganizationSettings } from '../hooks/useOrganizationSettings'
import type { MaturityLevel } from '../types'

interface MaturityTargetsSettingsProps {
  organizationId: string
}

type Draft = Record<string, '' | MaturityLevel>

/** The saved map keyed by upper-cased code, as the backend reads it. */
function normalise(saved: Record<string, string> | undefined): Draft {
  const out: Draft = {}
  for (const [code, level] of Object.entries(saved ?? {})) {
    out[code.trim().toUpperCase()] = level as MaturityLevel
  }
  return out
}

/** What the PATCH takes: every domain with a target, codes sorted; "No target" rows omitted. */
function toPayload(draft: Draft): Record<string, MaturityLevel> {
  const out: Record<string, MaturityLevel> = {}
  for (const code of Object.keys(draft).sort()) {
    const level = draft[code]
    if (level) out[code] = level
  }
  return out
}

/** The rows `draft` changes relative to `base`; '' marks a cleared target. */
function changesOf(base: Draft, draft: Draft): Draft {
  const out: Draft = {}
  for (const code of new Set([...Object.keys(base), ...Object.keys(draft)])) {
    if ((base[code] ?? '') !== (draft[code] ?? '')) out[code] = draft[code] ?? ''
  }
  return out
}

function messageOf(err: unknown, fallback: string): string {
  return err instanceof Error && err.message ? err.message : fallback
}

export default function MaturityTargetsSettings({ organizationId }: MaturityTargetsSettingsProps) {
  const queryClient = useQueryClient()
  const isAdmin = useIsOrgAdmin(organizationId)
  const settings = useOrganizationSettings(organizationId)
  const catalogue = useCatalogDomains()

  const saved = useMemo(() => normalise(settings.data?.maturity_targets), [settings.data?.maturity_targets])
  const [draft, setDraft] = useState<Draft>(saved)
  // The stored map the draft was taken from. A refetch replaces the draft only
  // while it holds no edits, so a background reload never drops unsaved work.
  const [base, setBase] = useState<Draft>(saved)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (Object.keys(changesOf(base, draft)).length > 0) return
    setDraft(saved)
    setBase(saved)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [saved])

  // Catalogue domains in catalogue order, then any stored code it no longer lists.
  const rows = useMemo(() => {
    const listed = (catalogue.data ?? []).map((d) => ({
      code: d.identifier.toUpperCase(),
      label: domainFilterLabel(d.identifier, d.name),
      listed: true,
    }))
    const known = new Set(listed.map((r) => r.code))
    const orphans = Object.keys(saved)
      .filter((code) => !known.has(code))
      .sort()
      .map((code) => ({ code, label: `${code} - not in the current catalogue`, listed: false }))
    return [...listed, ...orphans]
  }, [catalogue.data, saved])

  const changes = useMemo(() => changesOf(base, draft), [base, draft])
  const dirty = Object.keys(changes).length > 0
  const setCount = rows.filter((r) => !!draft[r.code]).length

  const handleSave = async () => {
    setSaving(true)
    setError(null)
    try {
      // Apply this admin's changes to the map as stored now, not as loaded.
      const fresh = await fetchOrganizationSettings(organizationId)
      const payload = toPayload({ ...normalise(fresh.maturity_targets), ...changes })
      await updateOrganizationSettings(organizationId, { maturity_targets: payload })
      const stored = normalise(payload)
      setDraft(stored)
      setBase(stored)
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['organization-settings', organizationId] }),
        // Every control's effective target on the list follows its domain's.
        queryClient.invalidateQueries({ queryKey: ['scoped-controls', organizationId] }),
      ])
      toast.success('Maturity targets saved')
    } catch (err) {
      setError(messageOf(err, 'Failed to save maturity targets'))
    } finally {
      setSaving(false)
    }
  }

  const loadError = settings.isError
    ? messageOf(settings.error, 'Could not load the organisation settings')
    : catalogue.isError
      ? messageOf(catalogue.error, 'Could not load the SCF domains')
      : null
  const loading = settings.isLoading || catalogue.isLoading

  return (
    <div className="settings-card maturity-targets-settings" data-testid="maturity-targets-settings">
      <h2>Maturity targets</h2>
      <p className="settings-card-sub">
        The maturity level each SCF domain&apos;s controls are meant to reach. Every in-scope control
        inherits its domain&apos;s target unless an administrator overrides it on the control&apos;s
        Governance tab. A domain with no target leaves its controls without one.
      </p>

      {loadError ? (
        <div role="alert" className="gov-error">{loadError}</div>
      ) : loading ? (
        <p className="settings-card-sub assessment-engine-muted">Loading…</p>
      ) : (
        <>
          <p className="settings-card-sub">
            {setCount} of {rows.length} domains have a target.
          </p>
          <div className="maturity-targets-grid">
            {rows.map((row) => (
              <label key={row.code} className="maturity-targets-row">
                <span className={row.listed ? 'maturity-targets-domain' : 'maturity-targets-domain assessment-engine-muted'}>
                  {row.label}
                </span>
                <select
                  className="form-control"
                  aria-label={`${row.code} target`}
                  value={draft[row.code] ?? ''}
                  disabled={!isAdmin || saving}
                  onChange={(event) => {
                    const value = event.target.value as '' | MaturityLevel
                    setDraft((prev) => ({ ...prev, [row.code]: value }))
                  }}
                >
                  <option value="">No target</option>
                  {MATURITY_LEVEL_OPTIONS.map((option) => (
                    <option key={option.value} value={option.value}>{option.label}</option>
                  ))}
                </select>
              </label>
            ))}
          </div>

          {error && <div role="alert" className="gov-error">{error}</div>}

          {isAdmin ? (
            <div className="settings-actions">
              <button className="btn-primary" disabled={saving || !dirty} onClick={() => void handleSave()}>
                {saving ? 'Saving...' : 'Save maturity targets'}
              </button>
            </div>
          ) : (
            <p className="settings-card-sub assessment-engine-muted">
              Only an organisation administrator can change maturity targets.
            </p>
          )}
        </>
      )}
    </div>
  )
}
