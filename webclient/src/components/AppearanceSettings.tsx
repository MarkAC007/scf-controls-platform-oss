/**
 * AppearanceSettings — organization branding (logo upload).
 * Theme selection lives in the header ThemeMenu; this page holds only
 * org-wide, admin-gated branding.
 */
import { useEffect, useRef, useState } from 'react'
import { toast } from 'react-hot-toast'
import { useQueryClient } from '@tanstack/react-query'
import { useOrgLogo, ORG_LOGO_QUERY_KEY } from '../hooks/useOrgLogo'
import {
  uploadOrganizationLogo,
  deleteOrganizationLogo,
  fetchOrganizationSettings,
  updateOrganizationSettings,
} from '../data/apiClient'
import type { BusinessSizeKey } from '../types'

// Keys match the catalogue's right-sizing guidance, so the recorded size marks
// the matching row on every control.
const BUSINESS_SIZE_OPTIONS: { value: BusinessSizeKey; label: string }[] = [
  { value: 'micro_small', label: 'Micro (fewer than 10 staff)' },
  { value: 'small', label: 'Small (10–49 staff)' },
  { value: 'medium', label: 'Medium (50–249 staff)' },
  { value: 'large', label: 'Large (250–999 staff)' },
  { value: 'enterprise', label: 'Enterprise (1,000+ staff)' },
]

interface AppearanceSettingsProps {
  organizationId: string
}

const MAX_LOGO_SIZE_BYTES = 1 * 1024 * 1024
const ALLOWED_LOGO_TYPES = ['image/png', 'image/jpeg', 'image/webp', 'image/svg+xml', 'image/gif']

export default function AppearanceSettings({ organizationId }: AppearanceSettingsProps) {
  const queryClient = useQueryClient()
  const { data: logoUrl } = useOrgLogo(organizationId)

  const logoFileInputRef = useRef<HTMLInputElement>(null)
  const [isUploadingLogo, setIsUploadingLogo] = useState(false)

  // Organisation metadata (name + industry) — rendered into generated document
  // headers. Loaded once and edited locally; `savedMeta` is the last persisted
  // state, so the Save button can stay disabled until something actually changed.
  const [orgName, setOrgName] = useState('')
  const [industry, setIndustry] = useState('')
  const [businessSize, setBusinessSize] = useState('')
  const [mission, setMission] = useState('')
  const [savedMeta, setSavedMeta] = useState({ name: '', industry: '', businessSize: '', mission: '' })
  const [isLoadingMeta, setIsLoadingMeta] = useState(true)
  // A failed load leaves the fields blank; saving then would wipe what is stored.
  const [metaLoadFailed, setMetaLoadFailed] = useState(false)
  const [isSavingMeta, setIsSavingMeta] = useState(false)

  useEffect(() => {
    let cancelled = false
    setIsLoadingMeta(true)
    setMetaLoadFailed(false)
    fetchOrganizationSettings(organizationId)
      .then((s) => {
        if (cancelled) return
        const next = {
          name: s.name ?? '',
          industry: s.industry ?? '',
          businessSize: s.business_size ?? '',
          mission: s.mission_statement ?? '',
        }
        setOrgName(next.name)
        setIndustry(next.industry)
        setBusinessSize(next.businessSize)
        setMission(next.mission)
        setSavedMeta(next)
      })
      .catch((err) => {
        if (!cancelled) {
          setMetaLoadFailed(true)
          toast.error(err instanceof Error ? err.message : 'Failed to load organisation details')
        }
      })
      .finally(() => {
        if (!cancelled) setIsLoadingMeta(false)
      })
    return () => {
      cancelled = true
    }
  }, [organizationId])

  const metaChanged =
    orgName !== savedMeta.name ||
    industry !== savedMeta.industry ||
    businessSize !== savedMeta.businessSize ||
    mission !== savedMeta.mission

  const handleMetaSave = async () => {
    const trimmedName = orgName.trim()
    if (!trimmedName) {
      toast.error('Organisation name cannot be blank')
      return
    }
    setIsSavingMeta(true)
    try {
      // Only what changed is sent, so a field someone else saved meanwhile
      // is not overwritten with this form's stale copy of it.
      const changes: Parameters<typeof updateOrganizationSettings>[1] = {}
      if (orgName !== savedMeta.name) changes.name = trimmedName
      // Empty input clears the field rather than storing an empty string —
      // doc-gen treats null as "omit the Industry line" (see tier1._header).
      if (industry !== savedMeta.industry) changes.industry = industry.trim() || null
      if (businessSize !== savedMeta.businessSize) {
        changes.business_size = (businessSize || null) as BusinessSizeKey | null
      }
      if (mission !== savedMeta.mission) changes.mission_statement = mission.trim() || null
      const saved = await updateOrganizationSettings(organizationId, changes)
      const next = {
        name: saved.name ?? '',
        industry: saved.industry ?? '',
        businessSize: saved.business_size ?? '',
        mission: saved.mission_statement ?? '',
      }
      setOrgName(next.name)
      setIndustry(next.industry)
      setBusinessSize(next.businessSize)
      setMission(next.mission)
      setSavedMeta(next)
      // Scoping reads business size from the cached settings.
      await queryClient.invalidateQueries({ queryKey: ['organization-settings'] })
      // The org name shows in the app header and org switcher, so refresh those.
      await queryClient.invalidateQueries({ queryKey: ['organization'] })
      toast.success('Organisation details saved')
    } catch (err) {
      // Non-admins get a 403 here; surface it rather than failing silently.
      toast.error(err instanceof Error ? err.message : 'Failed to save organisation details')
    } finally {
      setIsSavingMeta(false)
    }
  }

  const handleLogoUpload = async (event: React.ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0]
    event.target.value = ''
    if (!file) return
    if (!ALLOWED_LOGO_TYPES.includes(file.type)) {
      toast.error('Logo must be a PNG, JPEG, WebP, SVG, or GIF image')
      return
    }
    if (file.size > MAX_LOGO_SIZE_BYTES) {
      toast.error('Logo must be 1 MB or smaller')
      return
    }
    setIsUploadingLogo(true)
    try {
      await uploadOrganizationLogo(organizationId, file)
      await queryClient.invalidateQueries({ queryKey: [ORG_LOGO_QUERY_KEY, organizationId] })
      toast.success('Logo updated')
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Logo upload failed')
    } finally {
      setIsUploadingLogo(false)
    }
  }

  const handleLogoRemove = async () => {
    setIsUploadingLogo(true)
    try {
      await deleteOrganizationLogo(organizationId)
      await queryClient.invalidateQueries({ queryKey: [ORG_LOGO_QUERY_KEY, organizationId] })
      toast.success('Logo removed')
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Failed to remove logo')
    } finally {
      setIsUploadingLogo(false)
    }
  }

  return (
    <div className="appearance-settings settings-card">
      <h2>Organisation Branding</h2>

      <section className="appearance-section">
        <h3>Organisation Details</h3>
        <p className="appearance-hint">
          Used in the header and footer of every generated document. Requires the
          admin role to change.
        </p>
        <div className="org-meta-grid">
          <div className="org-meta-field">
            <label htmlFor="org-meta-name">Organisation Name</label>
            <input
              id="org-meta-name"
              type="text"
              className="org-meta-input"
              value={orgName}
              disabled={isLoadingMeta || isSavingMeta}
              maxLength={255}
              placeholder={isLoadingMeta ? 'Loading…' : 'e.g. Example Holdings Ltd.'}
              onChange={(e) => setOrgName(e.target.value)}
            />
            <span className="appearance-hint">Full legal or trading name.</span>
          </div>
          <div className="org-meta-field">
            <label htmlFor="org-meta-industry">Industry</label>
            <input
              id="org-meta-industry"
              type="text"
              className="org-meta-input"
              value={industry}
              disabled={isLoadingMeta || isSavingMeta}
              maxLength={255}
              placeholder={isLoadingMeta ? 'Loading…' : 'e.g. Technology'}
              onChange={(e) => setIndustry(e.target.value)}
            />
            <span className="appearance-hint">Omitted from documents when blank.</span>
          </div>
          <div className="org-meta-field">
            <label htmlFor="org-meta-business-size">Business Size</label>
            <select
              id="org-meta-business-size"
              className="org-meta-input"
              value={businessSize}
              disabled={isLoadingMeta || isSavingMeta}
              onChange={(e) => setBusinessSize(e.target.value)}
            >
              <option value="">Not recorded</option>
              {BUSINESS_SIZE_OPTIONS.map((o) => (
                <option key={o.value} value={o.value}>
                  {o.label}
                </option>
              ))}
            </select>
            <span className="appearance-hint">Marks the matching row of each control&apos;s right-sizing guidance.</span>
          </div>
          <div className="org-meta-field org-meta-field-wide">
            <label htmlFor="org-meta-mission">Mission Statement</label>
            <textarea
              id="org-meta-mission"
              className="org-meta-input"
              value={mission}
              disabled={isLoadingMeta || isSavingMeta}
              maxLength={4000}
              rows={3}
              placeholder={isLoadingMeta ? 'Loading…' : 'What the organisation exists to do'}
              onChange={(e) => setMission(e.target.value)}
            />
          </div>
        </div>
        <div className="appearance-actions">
          <button
            className="btn btn-primary"
            disabled={isLoadingMeta || isSavingMeta || metaLoadFailed || !metaChanged}
            onClick={handleMetaSave}
          >
            {isSavingMeta ? 'Saving…' : 'Save Organisation Details'}
          </button>
        </div>
      </section>

      <section className="appearance-section">
        <h3>Logo</h3>
        <p className="appearance-hint">
          Shown in the header for everyone in this organization. PNG, JPEG,
          WebP, SVG, or GIF up to 1 MB. Requires the admin role to change.
          (Personal themes are in the palette menu, top right.)
        </p>
        <div className="logo-settings-row">
          <div className="logo-preview">
            {logoUrl ? (
              <img src={logoUrl} alt="Organization logo" />
            ) : (
              <span className="logo-preview-empty">Using default logo</span>
            )}
          </div>
          <div className="appearance-actions">
            <button
              className="btn btn-secondary"
              disabled={isUploadingLogo}
              onClick={() => logoFileInputRef.current?.click()}
            >
              {isUploadingLogo ? 'Working…' : 'Upload logo…'}
            </button>
            {logoUrl && (
              <button className="btn btn-danger" disabled={isUploadingLogo} onClick={handleLogoRemove}>
                Remove logo
              </button>
            )}
            <input
              ref={logoFileInputRef}
              type="file"
              accept="image/png,image/jpeg,image/webp,image/svg+xml,image/gif"
              style={{ display: 'none' }}
              onChange={handleLogoUpload}
            />
          </div>
        </div>
      </section>
    </div>
  )
}
