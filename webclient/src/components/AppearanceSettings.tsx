/**
 * AppearanceSettings — organization branding (logo upload).
 * Theme selection lives in the header ThemeMenu; this page holds only
 * org-wide, admin-gated branding.
 */
import { useRef, useState } from 'react'
import { toast } from 'react-hot-toast'
import { useQueryClient } from '@tanstack/react-query'
import { useOrgLogo, ORG_LOGO_QUERY_KEY } from '../hooks/useOrgLogo'
import { uploadOrganizationLogo, deleteOrganizationLogo } from '../data/apiClient'

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
    <div className="appearance-settings card">
      <h2>Organization Branding</h2>

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
              className="btn"
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
