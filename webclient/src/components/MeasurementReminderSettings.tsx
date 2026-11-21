// SPDX-License-Identifier: AGPL-3.0-only
/**
 * MeasurementReminderSettings: Settings → Objective reminders.
 *
 * One choice: when in the period the owner of a security objective with a
 * logged metric is reminded that the period's value has not been entered.
 * The stored setting is a number of days: the reminder goes out once at most
 * that many days of the period remain, and never before the period starts,
 * so 366 means "from the first day of the period". Absent means 14.
 *
 * Reads the organisation settings through the shared hook; the PATCH is
 * admin-only, so the select renders read-only below admin.
 */
import { useCallback, useEffect, useState } from 'react'
import { toast } from 'react-hot-toast'

import { updateOrganizationSettings } from '../data/apiClient'
import { useOrganizationSettings } from '../hooks/useOrganizationSettings'
import { useOrgRoleState } from '../hooks/useHasOrgRole'

interface MeasurementReminderSettingsProps {
  organizationId: string
}

export const DEFAULT_REMINDER_DAYS = 14
/** At least as long as any period, so the reminder goes out on its first day. */
export const FROM_FIRST_DAY = 366

const LISTED: Array<{ days: number; label: string }> = [
  { days: 7, label: 'In the last 7 days of the period' },
  { days: 14, label: 'In the last 14 days of the period' },
  { days: 30, label: 'In the last 30 days of the period' },
  { days: FROM_FIRST_DAY, label: 'From the first day of the period' },
]

/** The listed choices, plus the saved value when it was set to something else through the API. */
export function reminderOptions(saved: number): Array<{ days: number; label: string }> {
  if (LISTED.some(option => option.days === saved)) return LISTED
  const own = { days: saved, label: `In the last ${saved} ${saved === 1 ? 'day' : 'days'} of the period` }
  return [...LISTED, own].sort((a, b) => a.days - b.days)
}

function messageOf(err: unknown, fallback: string): string {
  return err instanceof Error && err.message ? err.message : fallback
}

export default function MeasurementReminderSettings({ organizationId }: MeasurementReminderSettingsProps) {
  const { data: orgSettings, refetch } = useOrganizationSettings(organizationId)
  // The "only an administrator" line waits until the role is known to be missing.
  const { allowed: isAdmin, resolved: roleResolved } = useOrgRoleState(organizationId, 'admin')

  const savedDays = orgSettings?.isms_measurement_reminder_days ?? DEFAULT_REMINDER_DAYS
  const [days, setDays] = useState(savedDays)
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    setDays(savedDays)
  }, [savedDays])

  const handleSave = useCallback(async () => {
    setSaving(true)
    try {
      await updateOrganizationSettings(organizationId, { isms_measurement_reminder_days: days })
      toast.success('Measurement reminder timing saved')
      await refetch()
    } catch (err) {
      toast.error(messageOf(err, 'Failed to save the reminder timing'))
    } finally {
      setSaving(false)
    }
  }, [organizationId, days, refetch])

  return (
    <div className="settings-card measurement-reminder-settings" data-testid="measurement-reminder-settings">
      <h2>Objective reminders</h2>
      <p className="settings-card-sub">
        A security objective whose metric is logged by hand needs a value each period. This sets when
        its owner is reminded that the value for the current month, quarter or year has not been
        entered. One reminder is sent per period, and none once the value is logged. Reminders for an
        objective&apos;s due date and review date are separate: they go out 14 days ahead and again
        when the date passes.
      </p>

      <div className="org-meta-field">
        <label htmlFor="measurement-reminder-days">Remind the owner to log a measurement</label>
        <select
          id="measurement-reminder-days"
          className="org-meta-input"
          value={days}
          disabled={!isAdmin || saving}
          onChange={event => setDays(Number(event.target.value))}
        >
          {reminderOptions(savedDays).map(option => (
            <option key={option.days} value={option.days}>{option.label}</option>
          ))}
        </select>
      </div>

      {isAdmin ? (
        <div className="settings-actions">
          <button className="btn-primary" disabled={saving || days === savedDays} onClick={handleSave}>
            {saving ? 'Saving...' : 'Save reminder timing'}
          </button>
        </div>
      ) : roleResolved ? (
        <p className="settings-card-sub assessment-engine-muted">
          Only an organisation administrator can change the reminder timing.
        </p>
      ) : null}
    </div>
  )
}
