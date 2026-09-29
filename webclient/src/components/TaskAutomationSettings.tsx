/**
 * TaskAutomationSettings — Settings → Task automation.
 *
 * One switch: whether the platform creates evidence collection tasks on its
 * own. On (the default) means the nightly sweep and every tracking-record
 * save mint the next collection task for each tracked, scheduled evidence
 * item. Off means only tasks people create by hand exist.
 *
 * Reads the organisation settings through the shared hook; the PATCH is
 * admin-only, so the switch renders read-only below admin.
 */
import { useCallback, useEffect, useState } from 'react'
import { toast } from 'react-hot-toast'

import { updateOrganizationSettings } from '../data/apiClient'
import { useOrganizationSettings } from '../hooks/useOrganizationSettings'
import { useIsOrgAdmin } from '../hooks/useIsOrgAdmin'

interface TaskAutomationSettingsProps {
  organizationId: string
}

function messageOf(err: unknown, fallback: string): string {
  return err instanceof Error && err.message ? err.message : fallback
}

export default function TaskAutomationSettings({ organizationId }: TaskAutomationSettingsProps) {
  const { data: orgSettings, refetch } = useOrganizationSettings(organizationId)
  const isAdmin = useIsOrgAdmin(organizationId)

  // Absent means on: the backend's default, and what every organisation had
  // before the switch existed.
  const savedEnabled = orgSettings?.auto_task_generation_enabled ?? true
  const [enabled, setEnabled] = useState(savedEnabled)
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    setEnabled(savedEnabled)
  }, [savedEnabled])

  const handleSave = useCallback(async () => {
    setSaving(true)
    try {
      await updateOrganizationSettings(organizationId, { auto_task_generation_enabled: enabled })
      toast.success(enabled ? 'Automatic task creation switched on' : 'Automatic task creation switched off')
      await refetch()
    } catch (err) {
      toast.error(messageOf(err, 'Failed to save task automation'))
    } finally {
      setSaving(false)
    }
  }, [organizationId, enabled, refetch])

  return (
    <div className="settings-card task-automation-settings" data-testid="task-automation-settings">
      <h2>Task automation</h2>
      <p className="settings-card-sub">
        Whether collection tasks are created for you. When on, every tracked evidence item with a
        schedule gets its next collection task automatically — on save, and again each night for
        anything that fell due. When off, only tasks you create by hand exist.
      </p>

      <div className="trust-portal-toggle">
        <div className="toggle-row">
          <label htmlFor="auto-task-generation-toggle" className="toggle-label">
            <span className="toggle-label-text">Automatic task creation</span>
            <span className="toggle-label-hint">
              {enabled
                ? 'Collection tasks are created automatically from evidence schedules'
                : 'No tasks are created automatically; existing tasks are kept'}
            </span>
          </label>
          <button
            id="auto-task-generation-toggle"
            type="button"
            role="switch"
            aria-checked={enabled}
            aria-label="Automatic task creation"
            className={`toggle-switch ${enabled ? 'toggle-switch-on' : 'toggle-switch-off'}`}
            onClick={() => setEnabled(prev => !prev)}
            disabled={!isAdmin || saving}
          >
            <span className="toggle-switch-thumb" />
          </button>
        </div>
      </div>

      {isAdmin ? (
        <div className="settings-actions">
          <button
            className="btn-primary"
            disabled={saving || enabled === savedEnabled}
            onClick={handleSave}
          >
            {saving ? 'Saving...' : 'Save task automation'}
          </button>
        </div>
      ) : (
        <p className="settings-card-sub assessment-engine-muted">
          Only an organisation administrator can change task automation.
        </p>
      )}
    </div>
  )
}
