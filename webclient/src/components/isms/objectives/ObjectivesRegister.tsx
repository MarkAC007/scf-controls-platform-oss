// SPDX-License-Identifier: AGPL-3.0-only
/**
 * ObjectivesRegister — the ISMS profile's Objectives (clause 6.2) section.
 *
 * A register of security objective records, not part of the profile's draft:
 * every action (add, edit, confirm, log a value, record an evaluation,
 * archive, restore) saves on its own and refreshes the register, the ISMS
 * metrics dashboard and the documents list through one helper.
 *
 * Roles are resolved once here and passed down: editors act, admins confirm
 * and change the metric of a confirmed objective, viewers read.
 */
import { useId, useRef, useState, type JSX } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { toast } from 'react-hot-toast'
import { useConfirm } from '../../../contexts/ConfirmContext'
import { updateSecurityObjective, type ObjectiveOut } from '../../../data/securityObjectivesApi'
import { useHasOrgRole, useIsOrgEditor } from '../../../hooks/useHasOrgRole'
import { invalidateSecurityObjectives, useSecurityObjectives } from '../../../hooks/useSecurityObjectives'
import { navigateToDestination } from '../../AppLink'
import PillGroup from '../../analytics/PillGroup'
import { REGISTER_STATUS_FILTERS, registerFilter, teamOptions } from '../../analytics/ismsMetricFilters'
import ConfirmObjectiveModal from './ConfirmObjectiveModal'
import EvaluationModal from './EvaluationModal'
import LogValueModal from './LogValueModal'
import ObjectiveFormModal from './ObjectiveFormModal'
import ObjectivesTable, { type RowError } from './ObjectivesTable'
import ProposedBand from './ProposedBand'
import { errorText, summaryLine } from './objectiveText'

interface Props {
  organizationId: string
  focusObjectiveId?: string | null
}

export default function ObjectivesRegister({ organizationId, focusObjectiveId }: Props): JSX.Element {
  const queryClient = useQueryClient()
  const confirm = useConfirm()
  const canEdit = useIsOrgEditor(organizationId)
  const isAdmin = useHasOrgRole(organizationId, 'admin')
  const [showArchived, setShowArchived] = useState(false)
  // Team and status narrow the table only; view state, not in the URL.
  const [team, setTeam] = useState('')
  const [status, setStatus] = useState('')
  const teamId = useId()
  const objectives = useSecurityObjectives(organizationId, showArchived)

  const [adding, setAdding] = useState(false)
  const [editing, setEditing] = useState<ObjectiveOut | null>(null)
  const [confirming, setConfirming] = useState<ObjectiveOut | null>(null)
  const [logging, setLogging] = useState<ObjectiveOut | null>(null)
  const [evaluating, setEvaluating] = useState<ObjectiveOut | null>(null)
  const [rowError, setRowError] = useState<RowError | null>(null)
  const scrolledTo = useRef<string | null>(null)

  const setArchived = async (o: ObjectiveOut, archived: boolean) => {
    setRowError(null)
    if (archived) {
      const ok = await confirm({
        title: 'Archive this objective?',
        message: 'It leaves the register and the dashboard. Its results are kept and it can be restored.',
        confirmLabel: 'Archive',
        destructive: true,
      })
      if (!ok) return
    }
    try {
      await updateSecurityObjective(organizationId, o.id, { archived })
      toast.success(archived ? 'Objective archived' : 'Objective restored')
      await invalidateSecurityObjectives(queryClient, organizationId)
    } catch (err) {
      setRowError({ id: o.id, message: errorText(err) })
    }
  }

  const data = objectives.data
  const items = data?.items ?? []
  const proposed = items.filter((o) => o.lifecycle_status === 'proposed' && o.archived_at === null)
  const shown = registerFilter(items, team, status)
  const filtered = Boolean(team || status)
  const [count, ...rest] = data ? summaryLine(data.summary, proposed.length).split(' · ') : []

  return (
    <div className="obj-register">
      <div className="obj-toolbar">
        {data && (
          <p className="obj-toolbar-summary" data-testid="obj-summary">
            <strong>{count}</strong>
            {[...rest, ...(filtered ? [`showing ${shown.length}`] : [])].map((part) => ` · ${part}`).join('')}
          </p>
        )}
        {items.length > 0 && (
          <div className="obj-filters" role="group" aria-label="Filter objectives">
            <label className="ismsm-filter" htmlFor={teamId}>
              <span>Team</span>
              <select id={teamId} value={team} onChange={(e) => setTeam(e.target.value)}>
                <option value="">All teams</option>
                {teamOptions(items, team).map((t) => <option key={t.value} value={t.value}>{t.label}</option>)}
              </select>
            </label>
            <PillGroup
              label="Status"
              value={status}
              onChange={setStatus}
              options={REGISTER_STATUS_FILTERS.map((f) => ({ value: f.value, label: f.label }))}
            />
          </div>
        )}
        {canEdit && (
          <label className="obj-show-archived">
            <input type="checkbox" checked={showArchived} onChange={(e) => setShowArchived(e.target.checked)} />
            Show archived
          </label>
        )}
        <div className="obj-toolbar-actions">
          <button
            type="button"
            className="btn-secondary"
            onClick={() => navigateToDestination({ kind: 'isms-metrics' })}
          >
            Open ISMS metrics
          </button>
          {canEdit && (
            <button type="button" className="btn-primary" onClick={() => setAdding(true)}>
              Add objective
            </button>
          )}
        </div>
      </div>

      {objectives.isLoading ? (
        <p className="isms-empty">Loading the objectives…</p>
      ) : objectives.isError ? (
        <div role="alert" className="gov-error">The objectives could not be loaded: {errorText(objectives.error)}</div>
      ) : items.length === 0 ? (
        <p className="isms-empty">No objectives recorded yet.</p>
      ) : (
        <>
          {shown.length === 0 ? (
            <p className="isms-empty" data-testid="obj-no-match">
              No objectives match these filters.{' '}
              <button type="button" className="btn-link" onClick={() => { setTeam(''); setStatus('') }}>Clear filters</button>
            </p>
          ) : (
          <ObjectivesTable
            items={shown}
            canEdit={canEdit}
            isAdmin={isAdmin}
            focusObjectiveId={focusObjectiveId}
            rowError={rowError}
            scrolledTo={scrolledTo}
            onEdit={setEditing}
            onConfirm={setConfirming}
            onLog={setLogging}
            onEvaluate={setEvaluating}
            onArchive={(o) => void setArchived(o, true)}
            onRestore={(o) => void setArchived(o, false)}
          />
          )}
          <ProposedBand items={proposed} isAdmin={isAdmin} onConfirm={setConfirming} />
        </>
      )}

      {adding && <ObjectiveFormModal organizationId={organizationId} onClose={() => setAdding(false)} />}
      {editing && (
        <ObjectiveFormModal key={editing.id} organizationId={organizationId} objective={editing} onClose={() => setEditing(null)} />
      )}
      {confirming && (
        <ConfirmObjectiveModal key={confirming.id} organizationId={organizationId} objective={confirming} onClose={() => setConfirming(null)} />
      )}
      {logging && (
        <LogValueModal key={logging.id} organizationId={organizationId} objective={logging} onClose={() => setLogging(null)} />
      )}
      {evaluating && (
        <EvaluationModal key={evaluating.id} organizationId={organizationId} objective={evaluating} onClose={() => setEvaluating(null)} />
      )}
    </div>
  )
}
